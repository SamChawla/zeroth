// Where the browser should reach the API.
//
// The web service is a static file server on its own origin, so it cannot
// reach the API by relative path - the base URL has to be injected. This file
// holds the LOCAL development default; the web service's build step in
// zerops.yaml overwrites it with the deployed api subdomain, which is why it
// is a separate file rather than a line inside common.js.
window.ZEROTH_API = "http://localhost:8000";
