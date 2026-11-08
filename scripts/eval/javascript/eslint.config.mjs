// Comparison config: only the rules matched against vibepiler passes.
// Thresholds 0 so every function reports its number (agreement on values, not just flags).
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import sonarjs from "eslint-plugin-sonarjs";
const rules = {
  complexity: ["warn", { max: 0 }],
  "sonarjs/cognitive-complexity": ["warn", 0],
  "no-empty": ["warn", { allowEmptyCatch: false }],
};
export default [
  { linterOptions: { reportUnusedDisableDirectives: "off", noInlineConfig: true } },
  {
    files: ["**/*.{js,jsx,mjs,cjs}"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { sonarjs },
    rules: { ...rules, "no-unused-vars": ["warn", { args: "after-used", caughtErrors: "none", ignoreRestSiblings: true }] },
  },
  {
    files: ["**/*.{ts,tsx,mts,cts}"],
    languageOptions: { parser: tsParser, ecmaVersion: "latest", sourceType: "module", parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { sonarjs, "@typescript-eslint": tsPlugin },
    rules: { ...rules, "@typescript-eslint/no-unused-vars": ["warn", { args: "after-used", caughtErrors: "none", ignoreRestSiblings: true }] },
  },
];
