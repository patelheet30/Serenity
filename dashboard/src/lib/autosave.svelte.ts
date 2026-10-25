import { errorMessage } from './api';
import { handleUnauthorized } from './session.svelte';
import { toast } from './toast.svelte';

/** What the AutosaveIndicator in the top bar shows. */
export const autosave = $state({ status: 'idle' as 'idle' | 'saving' | 'saved' | 'error', savedAt: 0 });
let inflight = 0;

/** Run one settings write. Failures are toasted and resolve to undefined, so callers can revert. */
export async function save<T>(write: () => Promise<T>): Promise<T | undefined> {
	inflight++;
	autosave.status = 'saving';
	try {
		const result = await write();
		autosave.savedAt = Date.now();
		if (--inflight === 0 && autosave.status === 'saving') autosave.status = 'saved';
		return result;
	} catch (e) {
		inflight--;
		autosave.status = 'error';
		if (!handleUnauthorized(e)) toast(errorMessage(e), { error: true });
		return undefined;
	}
}
