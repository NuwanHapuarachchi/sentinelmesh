// Cognito hosted sign-in with the authorization-code + PKCE flow. Tokens live in sessionStorage only.
const Auth = (() => {
  let cfg;
  const redirect = () => location.origin + location.pathname.replace(/[^/]*$/, "");
  const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  async function init(config) {
    cfg = config;
    const params = new URLSearchParams(location.search);
    if (params.get("code")) {
      const body = new URLSearchParams({
        grant_type: "authorization_code", client_id: cfg.client_id, code: params.get("code"),
        redirect_uri: redirect(), code_verifier: sessionStorage.getItem("pkce") || "",
      });
      const r = await fetch(`${cfg.cognito_domain}/oauth2/token`, { method: "POST", body, headers: { "Content-Type": "application/x-www-form-urlencoded" } });
      if (r.ok) sessionStorage.setItem("tokens", JSON.stringify(await r.json()));
      history.replaceState({}, "", redirect());
    }
  }

  async function login() {
    const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
    sessionStorage.setItem("pkce", verifier);
    const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    const q = new URLSearchParams({
      response_type: "code", client_id: cfg.client_id, redirect_uri: redirect(), scope: "openid email profile",
      code_challenge_method: "S256", code_challenge: challenge,
    });
    location.href = `${cfg.cognito_domain}/oauth2/authorize?${q}`;
  }

  function logout() {
    sessionStorage.removeItem("tokens");
    location.href = `${cfg.cognito_domain}/logout?${new URLSearchParams({ client_id: cfg.client_id, logout_uri: redirect() })}`;
  }

  function claims() {
    const t = JSON.parse(sessionStorage.getItem("tokens") || "null");
    if (!t) return null;
    const c = JSON.parse(atob(t.id_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    if (c.exp * 1000 < Date.now()) { sessionStorage.removeItem("tokens"); return null; }
    return c;
  }

  const idToken = () => (claims() ? JSON.parse(sessionStorage.getItem("tokens")).id_token : null);
  return { init, login, logout, claims, idToken };
})();
