export type Me = { id: string; username: string; avatar_url: string | null };
export type Guild = {
	id: string;
	name: string;
	icon_url: string | null;
	manageable: boolean;
	installed: boolean;
};

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

export const LOGIN_URL = '/api/auth/login';
export const inviteUrl = (guildId?: string) =>
	guildId ? `/api/auth/invite?guild_id=${guildId}` : '/api/auth/invite';
