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

	{
		files: ["src/**/*.jsx"],
		ignores: ["src/components/ui/**"],
		rules: {
			"no-restricted-syntax": [
				"error",
				{
					selector: "JSXOpeningElement[name.type='JSXIdentifier'][name.name=/^(button|input|select|textarea|table|details|progress|a)$/]",
					message: "Use the shared UI component so appearance and interaction behavior remain consistent.",
				},
			],
		},
	},
	{
		files: ["src/components/ui/**/*.{js,jsx}"],
		rules: {
			"no-restricted-imports": [
				"error",
				{ patterns: [{ group: ["../**"], message: "UI components must not depend on feature workflows, routing or API clients." }] },
			],
		},
	},
];
