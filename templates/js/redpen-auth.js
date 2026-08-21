
(function(){
  // Shared auth client (stage 3, C.6): session/CSRF/Google-login plumbing,
  // used by both the editor panel and the /cabinet/ page. Holds only a CSRF
  // cache and the last-known user -- no editor-specific state.

  var _csrfToken = null;
  var _gisLoadPromise = null;

  function apiBase(path){
    var baseUrl = window.REDPEN_API_BASE || 'https://api.medinsky.net';
    return baseUrl + path;
  }

  function withJsonHeaders(headers){
    headers = headers || {};
    headers['Content-Type'] = 'application/json';
    return headers;
  }

  async function _fetchCsrf(isRetry){
    const res = await fetch(apiBase('/api/auth/csrf'), { credentials: 'include' });
    if (res.status === 403 && !isRetry) {
      _csrfToken = null;
      return _fetchCsrf(true);
    }
    if (!res.ok) throw new Error('csrf_failed');
    const data = await res.json();
    _csrfToken = data.csrfToken;
    return data;
  }

  // Returns {csrfToken}. Cached; pass forceRefresh=true to bypass the cache
  // (e.g. after a save request comes back 403 with a stale token).
  async function getCsrf(forceRefresh){
    if (_csrfToken && !forceRefresh) return { csrfToken: _csrfToken };
    try {
      return await _fetchCsrf(false);
    } catch (error) {
      console.error('[RedPenAuth] CSRF error:', error);
      throw error;
    }
  }

  // Returns null on 401 (not logged in); otherwise
  // {userId,email,name,pictureUrl,role,username}.
  async function me(){
    try {
      const res = await fetch(apiBase('/api/auth/me'), { credentials: 'include' });
      if (res.status === 401) return null;
      if (!res.ok) throw new Error('me_failed');
      const raw = await res.json();
      return {
        userId: raw.userId,
        email: raw.email,
        name: raw.name,
        pictureUrl: raw.picture,
        role: raw.role,
        username: raw.username,
      };
    } catch (error) {
      console.error('[RedPenAuth] me error:', error);
      throw error;
    }
  }

  async function loginWithToken(token){
    // No session exists yet, so /api/auth/csrf would 401 here; login is
    // protected by the token itself + SameSite=Lax instead of CSRF.
    const res = await fetch(apiBase('/api/auth/login'), {
      method: 'POST',
      headers: withJsonHeaders(),
      body: JSON.stringify({ token: token }),
      credentials: 'include',
    });
    if (!res.ok) throw new Error('login_failed');
    return me();
  }

  // invite — одноразовый код приглашения. Нужен только при первом входе:
  // круг участников закрыт, и попасть в него можно лишь по коду, переданному
  // вне системы (docs/anonymity-model.md). Дальше вход идёт без него.
  async function loginWithGoogle(credential, invite){
    const body = { credential: credential };
    if (invite) body.invite = invite;
    const res = await fetch(apiBase('/api/auth/google'), {
      method: 'POST',
      headers: withJsonHeaders(),
      body: JSON.stringify(body),
      credentials: 'include',
    });
    if (res.status === 403) throw new Error('invite_required');
    if (!res.ok) throw new Error('google_login_failed');
    return me();
  }

  async function setDisplayName(displayName){
    const csrf = await getCsrf();
    const res = await fetch(apiBase('/api/auth/display-name'), {
      method: 'POST',
      headers: withJsonHeaders({ 'X-CSRF-Token': csrf.csrfToken }),
      body: JSON.stringify({ displayName: displayName }),
      credentials: 'include',
    });
    if (!res.ok) throw new Error('display_name_failed');
    return res.json();
  }

  async function logout(){
    try {
      const csrf = await getCsrf();
      await fetch(apiBase('/api/auth/logout'), {
        method: 'POST',
        headers: withJsonHeaders({ 'X-CSRF-Token': csrf.csrfToken }),
        credentials: 'include',
      });
    } catch (error) {
      console.error('[RedPenAuth] logout error:', error);
    } finally {
      _csrfToken = null;
    }
  }

  function loadGis(cb){
    if (!_gisLoadPromise) {
      _gisLoadPromise = new Promise(function(resolve){
        if (window.google && window.google.accounts && window.google.accounts.id) { resolve(); return; }
        var script = document.createElement('script');
        script.src = 'https://accounts.google.com/gsi/client';
        script.async = true;
        script.defer = true;
        script.onload = function(){ resolve(); };
        script.onerror = function(){
          console.error('[RedPenAuth] Failed to load Google Identity Services');
          resolve();
        };
        document.head.appendChild(script);
      });
    }
    if (cb) _gisLoadPromise.then(cb);
    return _gisLoadPromise;
  }

  // onLogin(user) on success, onLogin(null, error) on failure.
  // getInvite() — необязательный колбэк, отдающий введённый код приглашения;
  // он нужен только тому, кто входит впервые.
  function renderGoogleButton(el, onLogin, getInvite){
    loadGis(function(){
      try {
        if (!window.google || !window.google.accounts || !window.google.accounts.id) return;
        if (!el) return;
        window.google.accounts.id.initialize({
          client_id: window.REDPEN_GOOGLE_CLIENT_ID,
          callback: async function(credentialResponse){
            try {
              var invite = getInvite ? getInvite() : null;
              var user = await loginWithGoogle(credentialResponse.credential, invite);
              if (onLogin) onLogin(user);
            } catch (error) {
              console.error('[RedPenAuth] Google login error:', error);
              if (onLogin) onLogin(null, error);
            }
          },
        });
        window.google.accounts.id.renderButton(el, { theme: 'outline', size: 'medium' });
      } catch (error) {
        console.error('[RedPenAuth] Google button error:', error);
      }
    });
  }

  window.RedPenAuth = {
    apiBase: apiBase,
    getCsrf: getCsrf,
    me: me,
    loginWithToken: loginWithToken,
    loginWithGoogle: loginWithGoogle,
    setDisplayName: setDisplayName,
    logout: logout,
    loadGis: loadGis,
    renderGoogleButton: renderGoogleButton,
  };
})();
