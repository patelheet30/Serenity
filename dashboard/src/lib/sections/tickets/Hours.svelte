<script lang="ts">
	import { api, send } from '../../api';
	import { save } from '../../autosave.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Icon from '../../components/Icon.svelte';
	import Select from '../../components/Select.svelte';
	import Toggle from '../../components/Toggle.svelte';
	import { session } from '../../session.svelte';
	import type { TicketPageProps } from '../Tickets.svelte';
	import { DAYS, discordText, fromGrid, scheduleText, toGrid, type Grid, type Schedule } from './hours';

	type TypeHours = { id: number; name: string; emoji: string | null; schedule: Schedule | null; open_off_hours: boolean };
	type Away = { until: number | null; until_local: string | null; note: string | null };
	type Hours = {
		schedule: Schedule | null;
		timezone: string;
		off_hours_message: string | null;
		default_message: string;
		away: Away | null;
		online: boolean;
		types: TypeHours[];
	};
	type Preview = { online: boolean; at: number | null; content: string | null };

	let { guildId, onerror }: TicketPageProps = $props();

	let data = $state<Hours | null>(null);
	// Unsaved edits; the preview shows them before they're saved.
	let grid: Grid = $state(toGrid(null));
	let message = $state('');
	let awayOn = $state(false);
	let awayUntil = $state('');
	let awayNote = $state('');
	let previewType: string = $state('');
	let preview: Preview | null = $state(null);
	let previewError: string | null = $state(null);

	function load(h: Hours) {
		data = h;
		grid = toGrid(h.schedule);
		message = h.off_hours_message ?? '';
		awayOn = !!h.away;
		awayUntil = h.away?.until_local ?? '';
		awayNote = h.away?.note ?? '';
	}

	$effect(() => {
		const id = guildId;
		let stale = false;
		data = null;
		api<Hours>(`/guilds/${id}/tickets/hours`).then((h) => !stale && load(h), (e) => !stale && onerror(e));
		return () => (stale = true);
	});

	const ZONES: string[] = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? ['UTC'];
	const gridText = $derived(fromGrid(grid));
	const awayBody = $derived(awayOn ? { until: awayUntil || null, note: awayNote.trim() || null } : null);
	const dirty = $derived(
		!!data &&
			(gridText !== scheduleText(data.schedule) ||
				message !== (data.off_hours_message ?? '') ||
				JSON.stringify(awayBody) !== JSON.stringify(data.away ? { until: data.away.until_local, note: data.away.note } : null))
	);

	async function patch(body: Record<string, unknown>) {
		const r = await save(() => send<Hours>('PATCH', `/guilds/${guildId}/tickets/hours`, body));
		if (!r) return r;
		if ('schedule' in body) load(r);
		else {
			// A timezone change keeps the unsaved drafts; an untouched away end is re-shown in the new zone.
			if (data?.away && awayUntil === data.away.until_local) awayUntil = r.away?.until_local ?? '';
			data = r;
		}
		return r;
	}

	// What away mode to send, for Save and the preview: a saved end that has passed since loading has
	// ended away mode, as the bot sees it (sending it would be refused as in the past).
	function sentAway() {
		const ended = !!data?.away?.until && data.away.until * 1000 <= Date.now() && awayUntil === data.away.until_local;
		return ended ? null : awayBody;
	}
	const saveAll = () => patch({ schedule: gridText || null, off_hours_message: message.trim() || null, away: sentAway() });


	// The preview is rendered by the bot from these drafts, 300 ms after the last edit.
	$effect(() => {
		if (!data) return;
		const body = {
			schedule: gridText || null,
			off_hours_message: message.trim() || null,
			away: sentAway(),
			...(previewType ? { type_id: Number(previewType) } : {})
		};
		let stale = false;
		const t = setTimeout(async () => {
			try {
				const p = await send<Preview>('POST', `/guilds/${guildId}/tickets/hours/preview`, body);
				if (!stale) ((preview = p), (previewError = null));
			} catch (e) {
				if (!stale) previewError = e instanceof Error ? e.message : String(e);
			}
		}, 300);
		return () => ((stale = true), clearTimeout(t));
	});

	const me = $derived(session.me);
</script>

{#if !data}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	<div class="grid">
		<section class="pane s7" aria-labelledby="whT">
			<div class="ph">
				<h2 id="whT">Support hours</h2>
				<span class="pill">{data.online ? '🟢 Online' : '🔴 Offline'}</span>
				<span class="sp"></span>
				<Select
					label="Timezone"
					align="right"
					value={data.timezone}
					options={[...new Set([data.timezone, ...ZONES])].map((z) => ({ value: z, label: z.replace(/_/g, ' ') }))}
					onchange={(z) => patch({ timezone: z })}
				/>
			</div>
			<div class="pb stack">
				<p class="hint">Tickets opened outside these hours get the auto-reply. Leave every day closed to turn hours off: staff then count as always around.</p>
				<div class="hours">
					{#each DAYS as day, d (day)}
						<div class="day">
							<span class="dname">{day}</span>
							<div class="ranges">
								{#each grid[d] as range, i (i)}
									<span class="range">
										<input class="inp" type="time" aria-label="{day} range {i + 1} start" bind:value={range[0]} />
										<span class="dim">–</span>
										<input class="inp" type="time" aria-label="{day} range {i + 1} end (00:00 is midnight)" bind:value={range[1]} />
										<button class="btn sm ghost" aria-label="Remove {day} range {i + 1}" onclick={() => grid[d].splice(i, 1)}><Icon name="x" /></button>
									</span>
								{:else}
									<span class="dim" style="font-size:13px">Closed</span>
								{/each}
							</div>
							<button class="btn sm ghost" aria-label="Add hours on {day}" onclick={() => grid[d].push(['09:00', '17:00'])}><Icon name="plus" /></button>
							{#if d < 6 && grid[d].length}
								<button class="btn sm ghost hide-s" title="Copy to the next day" aria-label="Copy {day}'s hours to {DAYS[d + 1]}" onclick={() => (grid[d + 1] = grid[d].map((r) => [...r] as [string, string]))}>→</button>
							{/if}
						</div>
					{/each}
				</div>
				<label class="field">
					<span>Off-hours message</span>
					<textarea class="inp" rows="3" maxlength="1000" placeholder={data.default_message} bind:value={message}></textarea>
					<span class="hint">Blank uses the default. Placeholders: <span class="mono">{'{user} {hours} {next_open}'}</span></span>
				</label>
				<div class="field">
					<div class="rowline" style="justify-content:flex-start">
						<Toggle label="Away mode" bind:checked={awayOn} />
						<span style="font-weight:500">Away mode</span>
						<span class="hint">Holidays, events: every new ticket gets the auto-reply.</span>
					</div>
					{#if awayOn}
						<div class="rowline" style="justify-content:flex-start;flex-wrap:wrap">
							<label class="field"><span>Back at ({data.timezone}, blank: until turned off)</span><input class="inp" type="datetime-local" bind:value={awayUntil} /></label>
							<label class="field" style="flex:1;min-width:180px"><span>Note added to the reply</span><input class="inp" maxlength="500" bind:value={awayNote} /></label>
						</div>
					{/if}
				</div>
				<div class="rowline">
					<span class="hint">{dirty ? 'Unsaved changes. The preview already shows them.' : 'Saved.'}</span>
					<div style="display:flex;gap:8px">
						<button class="btn" disabled={!dirty} onclick={() => load(data!)}>Discard</button>
						<button class="btn pri" disabled={!dirty} onclick={saveAll}><Icon name="check" />Save</button>
					</div>
				</div>
			</div>
		</section>

		<section class="pane s5" aria-labelledby="pvT">
			<div class="ph">
				<h2 id="pvT">Preview</h2>
				<span class="sp"></span>
				<Select
					label="Preview for type"
					align="right"
					bind:value={previewType}
					options={[{ value: '', label: 'Server hours' }, ...data.types.map((t) => ({ value: String(t.id), label: t.name }))]}
				/>
			</div>
			<div class="pb stack">
				{#if previewError}
					<p class="hint warn-t"><Icon name="warn" /> {previewError}</p>
				{:else if preview && !preview.content}
					<EmptyState>Support never closes with these hours, so nobody gets an auto-reply.</EmptyState>
				{:else if preview?.content}
					<p class="hint">
						{preview.online ? `Support is open now; this is what a ticket opened at ${new Date(preview.at! * 1000).toLocaleString()} would get.` : 'A ticket opened now gets this, in its channel:'}
					</p>
					<div class="dmsg">{@html discordText(preview.content, me ? { [me.id]: me.username } : {})}</div>
				{/if}
			</div>
		</section>

		<section class="pane s12" aria-labelledby="thT">
			<div class="ph">
				<h2 id="thT">Per-type hours</h2>
				<span class="cnt">{data.types.filter((t) => t.schedule).length} own</span>
				<span class="sp"></span>
				<a class="btn sm ghost" href="/dashboard/{guildId}/tickets/types">Edit on Types <Icon name="arrow" /></a>
			</div>
			<div class="scroll">
				<table class="tbl">
					<thead><tr><th>Type</th><th>Hours</th><th>Opening off-hours</th></tr></thead>
					<tbody>
						{#each data.types as t (t.id)}
							<tr>
								<td>{t.emoji ?? ''} {t.name}</td>
								<td class:dim={!t.schedule}>{t.schedule ? scheduleText(t.schedule) : 'Server hours'}</td>
								<td class="dim">{t.open_off_hours ? 'Allowed, with the auto-reply' : 'Blocked until support opens'}</td>
							</tr>
						{:else}
							<tr><td colspan="3"><EmptyState>No ticket types yet.</EmptyState></td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	</div>
{/if}

<style>
	/* An open picker lies over the panes after this one. */
	.grid > :global(.pane:focus-within) {
		position: relative;
		z-index: 2;
	}
	.hours {
		display: grid;
		gap: 8px;
	}
	.day {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.dname {
		width: 40px;
		font-size: 13px;
		font-weight: 500;
	}
	.ranges {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		flex: 1;
		min-width: 0;
	}
	.range {
		display: inline-flex;
		align-items: center;
		gap: 4px;
	}
	.range .inp {
		width: 108px;
	}
	.dmsg {
		padding: 10px 12px;
		border-left: 3px solid var(--line3);
		border-radius: var(--r);
		background: var(--ink);
		font-size: 14px;
		line-height: 1.45;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.dmsg :global(blockquote) {
		margin: 4px 0 0;
		padding-left: 10px;
		border-left: 3px solid var(--line3);
	}
	.dmsg :global(.mention) {
		color: var(--acc);
		background: var(--accbg);
		border-radius: 3px;
		padding: 0 2px;
	}
</style>
