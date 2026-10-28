import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// Physical direction utilities break RTL. Use logical ones (ms-/me-/ps-/pe-/start-/end-,
// text-start/text-end, rounded-s/e, border-s/e) instead.
const PHYSICAL =
  "/(^|[\\s:\"'`])-?(ml|mr|pl|pr|left|right|rounded-[lr]|rounded-[tb][lr]|border-[lr]|text-left|text-right|scroll-m[lr]|scroll-p[lr])(-|\\s|$)/";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "react/no-danger": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: `Literal[value=${PHYSICAL}]`,
          message: "Use logical direction utilities (ms-/me-/ps-/pe-/start-/end-) so RTL works.",
        },
        {
          selector: `TemplateElement[value.raw=${PHYSICAL}]`,
          message: "Use logical direction utilities (ms-/me-/ps-/pe-/start-/end-) so RTL works.",
        },
      ],
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Tokens and session data never go to web storage." },
        { name: "sessionStorage", message: "Tokens and session data never go to web storage." },
      ],
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "src/lib/api/schema.d.ts",
    "playwright-report/**",
    "test-results/**",
    ".lighthouse/**",
  ]),
]);

export default eslintConfig;
