import js from "@eslint/js";
import globals from "globals";

export default [
	{
		ignores: ["dist/**", "node_modules/**", "playwright-report/**", "test-results/**"],
	},
	{
		files: ["*.js", "e2e/**/*.{js,mjs}", "src/**/*.{js,jsx}"],
		languageOptions: {
			ecmaVersion: "latest",
			sourceType: "module",
			parserOptions: {
				ecmaFeatures: {
					jsx: true,
				},
			},
			globals: {
				...globals.browser,
				...globals.node,
				...globals.es2024,
			},
		},
		rules: {
			...js.configs.recommended.rules,
			"no-empty": ["error", { allowEmptyCatch: true }],
			"no-unused-vars": "off",
		},
	},
];
