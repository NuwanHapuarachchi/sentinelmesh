package lk.sentinelmesh.app

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URLEncoder
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

// Everything the app talks to is AWS: CloudFront for the public files, Cognito for a guest identity and
// IoT Core for the live alerts. No AWS SDK is needed: the calls are small and signed here.

object Net {
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .pingInterval(0, TimeUnit.SECONDS) // MQTT does its own keep-alive
        .build()

    /** GET a public file from CloudFront; null when it doesn't exist (yet). */
    fun getText(path: String): String? {
        val req = Request.Builder().url(BuildConfig.BASE_URL + path).header("Cache-Control", "no-cache").build()
        http.newCall(req).execute().use { r ->
            if (r.code == 403 || r.code == 404) return null
            if (!r.isSuccessful) throw java.io.IOException("HTTP ${r.code} for $path")
            return r.body.string()
        }
    }
}

data class AppConfig(val region: String, val identityPoolId: String, val iotEndpoint: String) {
    companion object {
        fun parse(json: String): AppConfig {
            val o = JSONObject(json)
            return AppConfig(o.getString("region"), o.optString("identity_pool_id"), o.optString("iot_endpoint"))
        }
    }
}

data class AwsCredentials(val accessKeyId: String, val secretKey: String, val sessionToken: String, val expiresAtMs: Long)

/** Cognito guest identity: no sign-up, just short-lived credentials that can only listen. */
object Cognito {
    private val JSON = "application/x-amz-json-1.1".toMediaType()

    private fun call(region: String, target: String, body: JSONObject): JSONObject {
        val req = Request.Builder()
            .url("https://cognito-identity.$region.amazonaws.com/")
            .header("X-Amz-Target", "AWSCognitoIdentityService.$target")
            .post(body.toString().toRequestBody(JSON))
            .build()
        Net.http.newCall(req).execute().use { r ->
            val text = r.body.string()
            if (!r.isSuccessful) throw java.io.IOException("Cognito $target failed (${r.code}): $text")
            return JSONObject(text)
        }
    }

    fun identityId(cfg: AppConfig, cached: String?): String =
        cached ?: call(cfg.region, "GetId", JSONObject().put("IdentityPoolId", cfg.identityPoolId)).getString("IdentityId")

    fun credentials(cfg: AppConfig, identityId: String): AwsCredentials {
        val c = call(cfg.region, "GetCredentialsForIdentity", JSONObject().put("IdentityId", identityId)).getJSONObject("Credentials")
        return AwsCredentials(
            c.getString("AccessKeyId"), c.getString("SecretKey"), c.getString("SessionToken"),
            (c.getDouble("Expiration") * 1000).toLong(),
        )
    }
}

/** SigV4 pre-signed WebSocket URL for AWS IoT Core (service "iotdevicegateway"). */
object SigV4 {
    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    private fun sha256Hex(s: String) = MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

    private fun hmac(key: ByteArray, s: String): ByteArray =
        Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }.doFinal(s.toByteArray())

    fun iotWebSocketUrl(endpoint: String, region: String, creds: AwsCredentials, now: Date = Date()): String {
        val service = "iotdevicegateway"
        val amzDate = SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(now)
        val day = amzDate.substring(0, 8)
        val scope = "$day/$region/$service/aws4_request"
        val query = "X-Amz-Algorithm=AWS4-HMAC-SHA256" +
            "&X-Amz-Credential=" + enc("${creds.accessKeyId}/$scope") +
            "&X-Amz-Date=$amzDate" +
            "&X-Amz-SignedHeaders=host"
        val canonical = "GET\n/mqtt\n$query\nhost:$endpoint\n\nhost\n${sha256Hex("")}"
        val toSign = "AWS4-HMAC-SHA256\n$amzDate\n$scope\n${sha256Hex(canonical)}"
        var key = hmac(("AWS4" + creds.secretKey).toByteArray(), day)
        key = hmac(key, region)
        key = hmac(key, service)
        key = hmac(key, "aws4_request")
        val signature = hmac(key, toSign).joinToString("") { "%02x".format(it) }
        // IoT Core expects the session token outside the signed part.
        return "wss://$endpoint/mqtt?$query&X-Amz-Signature=$signature&X-Amz-Security-Token=${enc(creds.sessionToken)}"
    }
}
