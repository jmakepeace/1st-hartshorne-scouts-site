// Routing Worker for the 1st Hartshorne Scouts site.
//
// Serves the static marketing site for everything EXCEPT the app subpaths,
// which are reverse-proxied to the existing K8s apps (behind the Cloudflare
// tunnel) so they live under the apex domain:
//   1sthartshornescouts.org/medication        -> medication.1sthartshornescouts.org
//   1sthartshornescouts.org/risk-assessments  -> ram.1sthartshornescouts.org
//   1sthartshornescouts.org/analytics         -> analytics.1sthartshornescouts.org
//
// The proxied apps are built with a matching Next.js basePath, so every link,
// asset and API URL they emit is already prefixed — we forward the path as-is.
// The apps' APP_URL is set to the apex, so their auth redirects target the apex
// too; `redirect: 'manual'` makes those 3xx pass through to the browser instead
// of being followed inside the Worker (which would break the SSO round-trip).
// Session cookies are host-only, so the browser binds them to the apex it sees.
//
// The analytics backend (Umami) is never dialed directly by browsers — the
// hostname above is an internal origin behind the tunnel only, reached
// server-side by this Worker, so visitors only ever see the apex domain.

const ROUTES = [
  { prefix: '/medication', host: 'medication.1sthartshornescouts.org' },
  { prefix: '/risk-assessments', host: 'ram.1sthartshornescouts.org' },
  { prefix: '/analytics', host: 'analytics.1sthartshornescouts.org' },
]

function matchRoute(pathname) {
  return ROUTES.find((r) => pathname === r.prefix || pathname.startsWith(r.prefix + '/'))
}

async function proxy(request, host) {
  const target = new URL(request.url)
  target.hostname = host
  target.port = ''
  target.protocol = 'https:'
  // redirect:'manual' must be set on the Request itself — passing it as the
  // fetch init is ignored when the first arg is a Request, which would make the
  // Worker follow the apps' SSO 3xx internally and break login.
  const base = new Request(target, request)
  base.headers.set('X-Forwarded-Host', new URL(request.url).host)
  base.headers.set('X-Forwarded-Proto', 'https')
  return fetch(new Request(base, { redirect: 'manual' }))
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url)
    const route = matchRoute(pathname)
    if (route) return proxy(request, route.host)
    // Everything else: the static site (ASSETS serves the file or its own 404).
    return env.ASSETS.fetch(request)
  },
}
