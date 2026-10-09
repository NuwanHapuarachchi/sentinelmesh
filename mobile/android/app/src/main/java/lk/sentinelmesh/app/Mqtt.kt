package lk.sentinelmesh.app

import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.ByteArrayOutputStream

/**
 * A minimal MQTT 3.1.1 client over WebSocket: just what a listener needs (CONNECT, SUBSCRIBE at QoS 1,
 * receive PUBLISH and PUBACK it, keep-alive pings). It never publishes; the guest role couldn't anyway.
 */
class MqttListener(
    private val url: String,
    private val clientId: String,
    private val topics: List<String>,
    private val keepAliveS: Int,
    private val onReady: () -> Unit,
    private val onMessage: (topic: String, payload: ByteArray) -> Unit,
    private val onClosed: (reason: String) -> Unit,
) {
    private var ws: WebSocket? = null
    private val buffer = ByteArrayOutputStream()
    @Volatile private var closed = false

    fun open() {
        val req = Request.Builder().url(url).header("Sec-WebSocket-Protocol", "mqtt").build()
        ws = Net.http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(connectPacket().toByteString())
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                synchronized(buffer) {
                    buffer.write(bytes.toByteArray())
                    drain()
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                finish("failure: ${t.message ?: t.javaClass.simpleName}${response?.let { " (HTTP ${it.code})" } ?: ""}")

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = finish("closed $code $reason")
        })
    }

    fun ping() {
        ws?.send(byteArrayOf(0xC0.toByte(), 0).toByteString())
    }

    fun close() {
        if (closed) return
        ws?.send(byteArrayOf(0xE0.toByte(), 0).toByteString())
        ws?.close(1000, "bye")
        finish("closed by app")
    }

    private fun finish(reason: String) {
        if (closed) return
        closed = true
        onClosed(reason)
    }

    /** Parse every complete MQTT packet in the buffer (WebSocket frames may split or join packets). */
    private fun drain() {
        var data = buffer.toByteArray()
        var pos = 0
        while (true) {
            if (data.size - pos < 2) break
            var mult = 1
            var len = 0
            var i = pos + 1
            var complete = false
            while (i < data.size) {
                val b = data[i].toInt() and 0xFF
                len += (b and 0x7F) * mult
                mult *= 128
                i++
                if (b and 0x80 == 0) { complete = true; break }
            }
            if (!complete || data.size - i < len) break
            handle(data[pos].toInt() and 0xFF, data.copyOfRange(i, i + len))
            pos = i + len
        }
        buffer.reset()
        if (pos < data.size) buffer.write(data, pos, data.size - pos)
        data = ByteArray(0)
    }

    private fun handle(header: Int, body: ByteArray) {
        when (header shr 4) {
            2 -> { // CONNACK
                val rc = body.getOrElse(1) { 0 }.toInt()
                android.util.Log.i("SentinelMqtt", "CONNACK rc=$rc session=${body.getOrElse(0) { 0 }}")
                if (rc != 0) { ws?.close(1000, "refused"); finish("connection refused ($rc)"); return }
                ws?.send(subscribePacket().toByteString())
            }
            9 -> { // SUBACK: one return code per topic; 0x80 means IoT Core refused it
                val codes = body.drop(2).map { it.toInt() and 0xFF }
                android.util.Log.i("SentinelMqtt", "SUBACK $codes")
                if (codes.any { it == 0x80 }) { ws?.close(1000, "subscribe refused"); finish("subscribe refused") } else onReady()
            }
            3 -> { // PUBLISH
                val qos = (header shr 1) and 3
                val topicLen = ((body[0].toInt() and 0xFF) shl 8) or (body[1].toInt() and 0xFF)
                val topic = String(body, 2, topicLen, Charsets.UTF_8)
                var p = 2 + topicLen
                if (qos > 0) {
                    val id0 = body[p]; val id1 = body[p + 1]
                    p += 2
                    ws?.send(byteArrayOf(0x40, 2, id0, id1).toByteString()) // PUBACK
                }
                android.util.Log.i("SentinelMqtt", "PUBLISH $topic (${body.size - p} bytes)")
                onMessage(topic, body.copyOfRange(p, body.size))
            }
            13 -> Unit // PINGRESP
        }
    }

    private fun str(s: String): ByteArray {
        val b = s.toByteArray(Charsets.UTF_8)
        return byteArrayOf((b.size shr 8).toByte(), b.size.toByte()) + b
    }

    private fun packet(header: Int, body: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        out.write(header)
        var x = body.size
        do {
            var d = x % 128
            x /= 128
            if (x > 0) d = d or 0x80
            out.write(d)
        } while (x > 0)
        out.write(body)
        return out.toByteArray()
    }

    // Clean session off: if the phone drops briefly, IoT Core keeps its subscription and queues QoS 1 alerts.
    private fun connectPacket() = packet(
        0x10,
        str("MQTT") + byteArrayOf(4, 0x00, (keepAliveS shr 8).toByte(), keepAliveS.toByte()) + str(clientId),
    )

    private fun subscribePacket(): ByteArray {
        var body = byteArrayOf(0, 1)
        for (t in topics) body += str(t) + byteArrayOf(1)
        return packet(0x82, body)
    }
}
