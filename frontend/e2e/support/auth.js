import path from "node:path";
import { fileURLToPath } from "node:url";

import dotenv from "dotenv";
import jwt from "jsonwebtoken";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "../../..");

dotenv.config({ path: path.join(repoRoot, ".env") });

export const STORAGE_AUTH_TOKEN = "tcg.auth.token";
export const STORAGE_AUTH_USER = "tcg.auth.user";
export const sampleRequirementsFile = path.join(repoRoot, "sample-requirements.md");

export function buildTestUser(overrides = {}) {
	return {
		sub: "playwright-e2e-user",
		email: "playwright-e2e@example.com",
		name: "Playwright E2E",
		picture: null,
		...overrides,
	};
}

export function buildTestAccessToken(user = buildTestUser()) {
	const secret = process.env.JWT_SECRET_KEY;
	if (!secret) {
		throw new Error("JWT_SECRET_KEY must be available in the repo .env for authenticated E2E tests.");
	}

	const algorithm = process.env.JWT_ALGORITHM || "HS256";
	const expirationMinutes = Number.parseInt(process.env.JWT_EXPIRATION_MINUTES || "60", 10);
	const now = Math.floor(Date.now() / 1000);
	const exp = now + Math.max(1, Number.isFinite(expirationMinutes) ? expirationMinutes : 60) * 60;

	return jwt.sign(
		{
			sub: user.sub,
			email: user.email,
			name: user.name,
			picture: user.picture,
			iat: now,
			exp,
		},
		secret,
		{ algorithm }
	);
}

export async function seedAuthenticatedSession(page, overrides = {}) {
	const user = buildTestUser(overrides);
	const token = buildTestAccessToken(user);

	await page.addInitScript(
		({ storageTokenKey, storageUserKey, authToken, authUser }) => {
			window.localStorage.setItem(storageTokenKey, authToken);
			window.localStorage.setItem(storageUserKey, JSON.stringify(authUser));
		},
		{
			storageTokenKey: STORAGE_AUTH_TOKEN,
			storageUserKey: STORAGE_AUTH_USER,
			authToken: token,
			authUser: user,
		}
	);

	return { token, user };
}
