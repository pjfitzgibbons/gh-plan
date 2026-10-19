// npm run lint. The extension runs in VS Code's Node; media/ runs in the webview, a browser.
const js = require("@eslint/js");
const globals = require("globals");

module.exports = [
  js.configs.recommended,
  { files: ["**/*.js"], languageOptions: { sourceType: "commonjs", globals: globals.node } },
  { files: ["media/**/*.js"], languageOptions: { sourceType: "script", globals: { ...globals.browser, acquireVsCodeApi: "readonly" } } },
  {
    rules: {
      eqeqeq: "error",
      "prefer-const": "error",
    },
  },
];
