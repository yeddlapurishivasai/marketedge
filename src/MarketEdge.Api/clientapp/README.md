# React + TypeScript + Vite

## India index options workspace

Open `/india/options` from the Indian Market menu. Application authentication is
shared with the rest of the SPA; Connect / Re-login opens the official Zerodha
login flow, and no broker credential is stored in the browser.

Choose NIFTY50/SENSEX and Daily, Weekly, Monthly or Expiry Intraday, then explicitly
Get Forecast. Daily targets roll to the next trading session at 14:00 IST; the
accepted target and snapshot cutoff are recorded on each immutable version.
The page polls persisted detail/job progress only while analysis or the final AI
selection is pending. Tab changes and reading history do not create forecast/AI
requests. Historical predictions remain readable without a live broker session.

Analysis, constituent evidence and all candidate legs/risk rules are kept separate
from the final AI candidate-ID selections. Selling only displays bounded-risk
hedged structures. History shows original predictions and versioned actuals plus
independent **hypothetical** strategy outcomes, not executed trades or portfolio
returns. Missing data, unavailable valuations and uncertain outcomes stay explicit.

History has a per-version Delete action with an explicit irreversible confirmation.
Known active forecasts, AI selections and jobs cannot be deleted. Deleting the
selected version opens the latest remaining saved version, or clears the view
when history is empty; it never generates a replacement forecast or AI call.
Stale deleted versions are removed on refresh. Permission and active-work
conflicts remain visible instead of silently retrying deletion. Shared broker
capture batches are retained by the server, not deleted with a history version.

Routine exchange calendars and spot-session hours are refreshed automatically
from public exchange sources; actual option expiries come from Zerodha. NIFTY50
weights use an explicitly dated, equity-normalized Nippon NiftyBeES holdings
proxy, not official/live index weights. Its provenance is visible in Analysis
and Constituents. The normal workspace has no metadata import, JSON editor or
administrator override section; the backend administration API remains available.

Analysis shows individual market observations grouped by category, not just
aggregate scores. Bullish/bearish labels require supplied directional evidence;
volatility, ADX, OI/PCR, basis, flows and liquidity are non-directional context.
Relative fills use finite stated scales. V5 signed influence fills use the supplied
coverage-adjusted directional weighted fraction on a fixed −1 to +1 scale, not
index points or probabilities. Context indicators have no directional fill.
Legacy snapshots retain explicitly raw, unnormalised vote/context labels; the
browser never reconstructs weights. Fundamental influences stay in Constituents.
Missing observation
timestamps are explicitly labelled as snapshot captures, not observed times.
Epoch sentinels, empty values and invalid timestamps display as **Unavailable**;
saved source payloads are not rewritten or assigned replacement observation times.

Information buttons open keyboard/touch-accessible, dismissible explanations.
Critical execution, quote age, gross/net and risk information stays visible.
Selected Selling watchlist cards expose hedged reference legs, expiry, target,
time exit and index-level breach exits. These are manual monitoring rules, not
orders, automatic stops or guaranteed fills. Missing stop levels remain unavailable.

Rates, dividend yield and fixed trading-cost estimates are optional. Unsupported
IV/Greeks/probabilities remain unavailable. Missing fees produce clearly labelled
gross payoffs, never zero-cost net profit. Quote freshness and market-session
entry restrictions still apply.

Frontend checks (no additional test packages required):

```powershell
npm run build
npx --no-install eslint src\optionsApi.ts src\pages\OptionsPage.tsx
node --test tests\optionsApi.test.mjs tests\optionsDisplay.test.mjs
```

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
