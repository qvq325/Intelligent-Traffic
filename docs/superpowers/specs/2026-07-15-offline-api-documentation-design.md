# Offline API Documentation Design

## Problem

The `/docs` route returns `200`, and `/openapi.json` returns a valid schema, but
the generated Swagger UI page loads its stylesheet, JavaScript bundle, and
favicon from public Internet hosts. When those hosts are unavailable, the
browser receives the HTML shell but cannot initialize Swagger UI, leaving the
page blank or loading indefinitely.

## Chosen Approach

Vendor the Apache-2.0 `swagger-ui-dist` 5.32.8 browser assets with the
application. This avoids a new runtime dependency and makes the primary API
documentation route independent of Internet access.

The repository will contain only the browser files used by the page and the
license materials required by their distribution:

- `frontend/vendor/swagger-ui/swagger-ui-bundle.js`
- `frontend/vendor/swagger-ui/swagger-ui.css`
- `frontend/vendor/swagger-ui/LICENSE`
- `frontend/vendor/swagger-ui/NOTICE`
- `frontend/vendor/swagger-ui/swagger-ui-bundle.js.LICENSE.txt`

The upstream package version and source will also be recorded in
`THIRD_PARTY_NOTICES.md`. Source maps and unused distribution files will not be
included.

## Application Integration

Disable FastAPI's generated Swagger route by constructing the application with
`docs_url=None`. Register a replacement `/docs` route with FastAPI's
`get_swagger_ui_html` helper and point it at the vendored files under
`/static/vendor/swagger-ui/`. Use a data URL for the favicon so the page has no
remaining external resource request.

Preserve the existing `/openapi.json` schema URL, page title, Swagger UI
parameters, and `/docs/oauth2-redirect` behavior. The secondary `/redoc` route
is unchanged; the application and README expose `/docs` as the supported API
documentation entry point.

Request flow after the change:

1. The browser requests `/docs` from VideoTest.
2. The returned HTML references only same-origin `/static/vendor/swagger-ui/`
   assets and `/openapi.json`.
3. The browser loads the local bundle and stylesheet.
4. Swagger UI requests the existing OpenAPI schema and renders the operations.

There is no CDN fallback. A fallback would retain the network timeout that
causes the current symptom and would make failures nondeterministic. Missing
vendored files instead produce a direct local `404`, which regression tests
will detect.

## Compatibility And Scope

No API route contract, OpenAPI schema, frontend navigation link, database
schema, or configuration format changes. `/docs`, `/docs/oauth2-redirect`, and
`/openapi.json` keep their existing public paths. This fix does not redesign
the documentation UI or change the unrelated `/redoc` implementation.

GitNexus reports MEDIUM impact for `create_app`: eight direct callers, no
affected execution flows, and one affected module consisting of application
tests. There is no HIGH or CRITICAL risk warning.

## Verification

- Add a failing regression test before the application change.
- Verify `/docs`, `/openapi.json`, the local JavaScript bundle, and the local
  stylesheet all return `200`.
- Verify the `/docs` HTML references the same-origin assets and contains no
  `http://` or `https://` resource URL.
- Verify the OAuth redirect route remains available.
- Run the focused API test module and the complete pytest suite.
- Restart the local service and repeat HTTP checks against the configured
  port.
- Run GitNexus `detect_changes()` and confirm that only the documentation
  setup and its regression coverage are affected.
