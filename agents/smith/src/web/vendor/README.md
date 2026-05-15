# vendor/

Immutable browser UMD blobs that the page bundles inline. Not npm deps —
vendored on purpose so installs can't break the UI and there's nothing
to resolve at run-time.

Bump procedure: replace the file with a fresh UMD copy from
`node_modules` in any React/Babel-using project, and update the pinned
version below. Re-verify the page boots.

Pinned versions:

| File                              | Pkg                  | Version  |
|-----------------------------------|----------------------|----------|
| `react.production.min.js`         | `react`              | 18.3.1   |
| `react-dom.production.min.js`     | `react-dom`          | 18.3.1   |
| `babel.min.js`                    | `@babel/standalone`  | 7.28.5   |

These three are wrapped into both `/chat` and `/briefs` page HTML by
`src/server.ts → renderPage()`. The browser needs zero external network
to render.
