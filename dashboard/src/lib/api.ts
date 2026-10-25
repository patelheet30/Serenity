export type Me = { id: string; username: string; avatar_url: string | null };
export type Guild = {
	id: string;
	name: string;
	icon_url: string | null;
	manageable: boolean;
	installed: boolean;
};

export type Channel = {
	id: string;
	name: string;
	type: 'text' | 'announcement' | 'category' | 'voice' | 'stage' | 'forum';
	parent_id: string | null;
	position: number;
};
export type Modules = { enabled: Record<string, boolean>; requires: Record<string, string[]> };

export class ApiError extends Error {
	constructor(
		public status: number,
		public code: string,
		message: string,
		public invite?: string
	) {
		super(message);
	}
}

/** Call the bot's /api. Errors arrive as {"error": {code, message, ...}}. */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
	const res = await fetch(`/api${path}`, { credentials: 'same-origin', ...init });
	if (res.status === 204) return undefined as T;
	const body = await res.json().catch(() => null);
	if (!res.ok) {
		const e = body?.error ?? {};
		throw new ApiError(res.status, e.code ?? 'http_error', e.message ?? res.statusText, e.invite);
	}
	return body as T;
}

/** A JSON write (PUT/PATCH/POST/DELETE); resolves to the response body. */
export function send<T>(method: string, path: string, body?: unknown): Promise<T> {
	return api<T>(path, {
		method,
		headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	});
}

export const errorMessage = (e: unknown) => (e instanceof Error ? e.message : String(e));

export const LOGIN_URL = '/api/auth/login';
export const inviteUrl = (guildId?: string) =>
	guildId ? `/api/auth/invite?guild_id=${guildId}` : '/api/auth/invite';
