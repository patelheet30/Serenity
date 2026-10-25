import { goto } from '$app/navigation';
import { api, ApiError, type Guild, type Me } from './api';

export const session = $state({
	me: null as Me | null,
	guilds: null as Guild[] | null
});

/** If `e` says the session is gone (expired, logged out elsewhere), forget it and go home. */
export function handleUnauthorized(e: unknown): boolean {
	if (!(e instanceof ApiError && e.status === 401)) return false;
	session.me = null;
	session.guilds = null;
	goto('/');
	return true;
}

/** The logged-in user, or a redirect to the landing page. Other failures throw. */
export async function requireMe(): Promise<Me | null> {
	if (session.me) return session.me;
	try {
		session.me = await api<Me>('/me');
	} catch (e) {
		if (handleUnauthorized(e)) return null;
		throw e;
	}
	return session.me;
}

export async function loadGuilds(refresh = false): Promise<Guild[]> {
	if (!session.guilds || refresh) session.guilds = await api<Guild[]>('/guilds');
	return session.guilds;
}

export async function logout() {
	await api('/auth/logout', { method: 'POST' });
	session.me = null;
	session.guilds = null;
	goto('/');
}
