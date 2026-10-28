<script lang="ts">
	import { api, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import Select from '../../components/Select.svelte';
	import { toast } from '../../toast.svelte';
	import type { TicketPageProps } from '../Tickets.svelte';
	import type { Role } from './settings';

	type Blocked = { user_id: string; name: string | null; moderator_id: string; reason: string | null; created_at: number };
	type Settings = {
		retention_days: number;
		rating_window_days: number;
		open_cooldown_minutes: number;
		raid_limit: number;
		raid_window_minutes: number;
		min_account_age_days: number;
		raid_paused: boolean;
		required_role_id: string | null;
		log_channel_id: string | null;
		log_enabled: boolean;
		blacklist: Blocked[];
	};
	type NumKey = 'retention_days' | 'rating_window_days' | 'open_cooldown_minutes' | 'raid_limit' | 'raid_window_minutes' | 'min_account_age_days';
	type Member = { id: string; username: string; display_name: string };

	let { guildId, channels, onerror }: TicketPageProps = $props();

	let data: Settings | null = $state(null);
	let roles: Role[] = $state([]);
	let members: Member[] = $state([]);
	let blockUser: string | null = $state(null);
	let blockReason = $state('');

	$effect(() => {
		const id = guildId;
		let stale = false;
		data = null;
		Promise.all([api<Settings>(`/guilds/${id}/tickets/settings`), api<Role[]>(`/guilds/${id}/roles`)]).then(
			([s, r]) => !stale && ((data = s), (roles = r.filter((x) => !x.managed && x.id !== id))),
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	const postable = $derived(channels.filter((c) => c.type === 'text' || c.type === 'announcement'));
	const NONE = '0'; // the "no role" option

	async function patch(body: Partial<Settings>) {
		const r = await save(() => send<Settings>('PATCH', `/guilds/${guildId}/tickets/settings`, body));
		if (r) data = r;
		return r;
	}

	async function setNum(key: NumKey, input: HTMLInputElement) {
		const v = Number(input.value);
		if (v === data![key]) return;
		// The server checks the limits; a refused value goes back to the saved one.
		if (!(await patch({ [key]: Number.isInteger(v) ? v : NaN }))) input.value = String(data![key]);
	}

	async function setLog(id: string) {
		const r = await save(() => send('PUT', `/guilds/${guildId}/logging/ticket`, { channel_id: id }));
		if (r) data = { ...data!, log_channel_id: id, log_enabled: true };
	}

	async function searchMembers(q: string) {
		try {
			members = await api<Member[]>(`/guilds/${guildId}/members?query=${encodeURIComponent(q)}`);
		} catch (e) {
			onerror(e);
		}
	}

	async function block(e: SubmitEvent) {
		e.preventDefault();
		if (!blockUser) return;
		const r = await save(() =>
			send<Settings>('POST', `/guilds/${guildId}/tickets/blacklist`, { user_id: blockUser, reason: blockReason.trim() || null })
		);
		if (!r) return;
		data = r;
		blockUser = null;
		blockReason = '';
	}

	async function unblock(b: Blocked) {
		const r = await save(() => send<Settings>('DELETE', `/guilds/${guildId}/tickets/blacklist/${b.user_id}`));
		if (!r) return;
		data = r;
		toast(`${b.name ?? b.user_id} can open tickets again`, {
			undo: async () => {
				const u = await save(() =>
					send<Settings>('POST', `/guilds/${guildId}/tickets/blacklist`, { user_id: b.user_id, reason: b.reason })
				);
				if (u) data = u;
			}
		});
	}
</script>

{#snippet num(key: NumKey, label: string, min: number, max: number, unit: string, hint: string)}
	<label class="field">
		<span>{label}</span>
		<div class="rowline" style="justify-content:flex-start">
			<input class="inp" style="width:96px" type="number" inputmode="numeric" {min} {max} value={data![key]} onchange={(e) => setNum(key, e.currentTarget)} />
			<span class="hint">{unit}</span>
		</div>
		<span class="hint">{hint}</span>
	</label>
{/snippet}

{#if !data}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	<div class="grid">
		<section class="pane s6" aria-labelledby="opT">
			<div class="ph"><h2 id="opT">Opening tickets</h2></div>
			<div class="pb stack">
				{@render num('open_cooldown_minutes', 'Cooldown', 0, 1440, 'minutes', 'How long a member waits between opening tickets. 0 turns it off.')}
				{@render num('min_account_age_days', 'Minimum account age', 0, 365, 'days', 'Discord account age needed to open a ticket. 0: none.')}
				<div class="field">
					<span>Required role</span>
					<Select
						label="Role needed to open tickets"
						value={data.required_role_id ?? NONE}
						options={[{ value: NONE, label: 'No role needed' }, ...roles.map((r) => ({ value: r.id, label: `@${r.name}` }))]}
						onchange={(id) => patch({ required_role_id: id === NONE ? null : id })}
					/>
				</div>
			</div>
		</section>

		<section class="pane s6" aria-labelledby="rbT">
			<div class="ph"><h2 id="rbT">Raid brake</h2></div>
			<div class="pb stack">
				{#if data.raid_paused}
					<p class="hint warn-t">
						<Icon name="warn" /> Opening is paused because too many tickets opened at once.
						<button class="linkish" onclick={() => patch({ raid_paused: false })}>Resume opening tickets</button>
					</p>
				{/if}
				{@render num('raid_limit', 'Pause once this many open', 0, 100, 'tickets', '0 turns the brake off.')}
				{@render num('raid_window_minutes', 'Within', 1, 60, 'minutes', 'Staff are pinged in the ticket log with a button to resume. Changing either resumes opening.')}
			</div>
		</section>

		<section class="pane s6" aria-labelledby="trT">
			<div class="ph"><h2 id="trT">Transcripts and ratings</h2></div>
			<div class="pb stack">
				{@render num('retention_days', 'Keep transcripts for', 7, 365, 'days', 'After a ticket closes. Types can override it; already-closed tickets keep their expiry.')}
				{@render num('rating_window_days', 'Rating window', 1, 30, 'days', 'How long openers can rate a closed ticket. Turn ratings off per type.')}
			</div>
		</section>

		<section class="pane s6" aria-labelledby="lgT">
			<div class="ph"><h2 id="lgT">Ticket log</h2></div>
			<div class="pb stack">
				<div class="field">
					<span>Channel</span>
					<Select
						label="Ticket log channel"
						placeholder="Choose channel…"
						value={data.log_channel_id}
						options={postable.map((c) => ({ value: c.id, label: `#${c.name}` }))}
						onchange={setLog}
					/>
					<span class="hint">
						Ticket events, raid-brake alerts and ratings of 2 or less go here.
						{#if data.log_channel_id && !data.log_enabled}<span class="warn-t">It’s turned off on the Logging page.</span>{/if}
					</span>
				</div>
			</div>
		</section>

		<section class="pane s12" aria-labelledby="blT">
			<div class="ph"><h2 id="blT">Blacklist</h2><span class="cnt">{data.blacklist.length}</span></div>
			<div class="scroll">
				<table class="tbl">
					<thead><tr><th>Member</th><th>Reason</th><th>Blocked</th><th><span class="sr">Unblock</span></th></tr></thead>
					<tbody>
						{#each data.blacklist as b (b.user_id)}
							<tr>
								<td><span class="chname"><Icon name="user" />{b.name ?? b.user_id}</span></td>
								<td class="dim" style="white-space:normal">{b.reason ?? '-'}</td>
								<td class="dim">{new Date(b.created_at * 1000).toLocaleDateString()}</td>
								<td class="r"><button class="btn sm ghost" aria-label="Unblock {b.name ?? b.user_id}" onclick={() => unblock(b)}><Icon name="x" /></button></td>
							</tr>
						{:else}
							<tr><td colspan="4"><EmptyState>Nobody is blocked from opening tickets.</EmptyState></td></tr>
						{/each}
					</tbody>
				</table>
			</div>
			<form class="addrow" onsubmit={block}>
				<div class="field" style="flex:1;min-width:160px">
					<span>Member</span>
					<Select
						label="Member to block"
						placeholder="Search members…"
						bind:value={blockUser}
						options={members.map((m) => ({ value: m.id, label: m.display_name, hint: m.username }))}
						onsearch={searchMembers}
					/>
				</div>
				<label class="field" style="flex:2;min-width:160px">
					<span>Reason (staff only)</span>
					<input class="inp" maxlength="500" bind:value={blockReason} />
				</label>
				<button class="btn pri" type="submit" disabled={!blockUser}><Icon name="plus" />Block</button>
			</form>
		</section>
	</div>
{/if}
