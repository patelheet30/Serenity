<script lang="ts">
	import { api, send, type Modules } from '../api';
	import { save } from '../autosave.svelte';
	import Avatar from '../components/Avatar.svelte';
	import DataTable from '../components/DataTable.svelte';
	import EmptyState from '../components/EmptyState.svelte';
	import Icon from '../components/Icon.svelte';
	import Modal from '../components/Modal.svelte';
	import { palette, registerCommands } from '../palette.svelte';
	import { toast } from '../toast.svelte';
	import { ago, isTyping, MOD } from '../util';
	import { ACTIONS as ACTION_META, duration, type Case, type CaseUser } from './moderation';

	type Detail = Case & { history: Case[]; warnings: number };
	type Page = {
		cases: Case[];
		total: number;
		all: number;
		next: number | null;
		facets: { action: Record<string, number>; moderator: (CaseUser & { count: number })[] };
	};

	let { guildId, onerror }: { guildId: string; onerror: (e: unknown) => void } = $props();

	const ACTIONS = Object.keys(ACTION_META);
	const who = (u: CaseUser) => u.name ?? u.id;

	let q = $state('');
	let search = $state(''); // q, 250 ms after the last keystroke
	let acts = $state<string[]>([]);
	let mods = $state<string[]>([]);
	let user = $state<CaseUser | null>(null);
	let active = $state(false);

	let page = $state<Page | null>(null);
	let rows = $state<Case[]>([]);
	let modules = $state<Modules | null>(null);
	let selected = $state<number | null>(null);
	let detail = $state<Detail | null>(null);
	let sheet = $state(false); // the detail's bottom sheet on phones
	let editing = $state(false);
	let draft = $state('');
	let reasonInput = $state<HTMLTextAreaElement>();
	// Focus by hand: autofocus is ignored while a table row has focus (the `e` shortcut).
	$effect(() => {
		if (editing) reasonInput?.focus();
	});
	let confirmClear = $state(false);
	let version = $state(0); // bump to reload the list after a write changes statuses

	const filtered = $derived(acts.length + mods.length > 0 || !!user || active || !!q);

	$effect(() => {
		const v = q;
		const t = setTimeout(() => (search = v), 250);
		return () => clearTimeout(t);
	});

	function params(before?: number) {
		const p = new URLSearchParams();
		if (acts.length) p.set('action', acts.join(','));
		if (mods.length) p.set('moderator', mods.join(','));
		if (user) p.set('user', user.id);
		if (active) p.set('active', '1');
		if (search.trim()) p.set('q', search.trim());
		if (before) p.set('before', String(before));
		return p;
	}

	$effect(() => {
		const id = guildId;
		let stale = false;
		api<Modules>(`/guilds/${id}/modules`).then((m) => !stale && (modules = m), (e) => !stale && onerror(e));
		return () => (stale = true);
	});

	// The first page, again whenever a filter changes.
	let generation = 0; // bumped per first-page load; older "Load more" answers are dropped
	$effect(() => {
		const id = guildId;
		const p = params();
		version;
		generation++;
		let stale = false;
		api<Page>(`/guilds/${id}/cases?${p}`).then(
			(r) => {
				if (stale) return;
				page = r;
				rows = r.cases;
				if (selected === null && r.cases.length && innerWidth > 980) open(r.cases[0].number, false);
			},
			(e) => !stale && onerror(e)
		);
		return () => (stale = true);
	});

	let more = $state(false);
	async function loadMore() {
		if (!page?.next || more) return;
		more = true;
		const gen = generation;
		try {
			// Later pages carry only cases and the cursor; facets stay from the first page.
			const r = await api<Pick<Page, 'cases' | 'next'>>(`/guilds/${guildId}/cases?${params(page.next)}`);
			if (gen !== generation || !page) return; // the filters changed meanwhile
			rows.push(...r.cases);
			page.next = r.next;
		} catch (e) {
			if (gen === generation) toast(e instanceof Error ? e.message : String(e), { error: true });
		} finally {
			more = false;
		}
	}

	let detailFor = 0; // the newest open() wins if answers arrive out of order
	async function open(n: number, asSheet = true) {
		selected = n;
		editing = false;
		sheet = asSheet;
		const req = ++detailFor;
		try {
			const d = await api<Detail>(`/guilds/${guildId}/cases/${n}`);
			if (req === detailFor) detail = d;
		} catch (e) {
			if (req === detailFor) onerror(e);
		}
	}

	function close() {
		detailFor++; // an open() still in flight must not bring the pane back
		selected = null;
		detail = null;
		sheet = false;
	}

	async function saveReason(e?: Event) {
		e?.preventDefault();
		if (!detail) return;
		const n = detail.number;
		const r = await save(() => send<Detail>('PATCH', `/guilds/${guildId}/cases/${n}`, { reason: draft }));
		if (!r) return;
		editing = false;
		if (detail?.number === n) detail = r;
		const row = rows.find((c) => c.number === n);
		if (row) row.reason = r.reason;
		toast(`Case #${n} reason updated`);
	}

	async function clearWarnings() {
		confirmClear = false;
		if (!detail) return;
		const target = detail.target;
		const r = await save(() => send<{ cleared: number }>('POST', `/guilds/${guildId}/users/${target.id}/clear-warnings`));
		if (!r) return;
		toast(r.cleared ? `Cleared ${r.cleared} warning${r.cleared === 1 ? '' : 's'} for ${who(target)}` : `${who(target)} had no active warnings`);
		if (detail) open(detail.number, sheet);
		version++;
	}

	function toggle(list: string[], v: string) {
		return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
	}

	function clearFilters() {
		q = '';
		search = '';
		acts = [];
		mods = [];
		user = null;
		active = false;
	}

	function onkeydown(e: KeyboardEvent) {
		if (!detail || isTyping(e) || palette.open || e.metaKey || e.ctrlKey || e.altKey) return;
		if (e.key === 'e') {
			e.preventDefault();
			draft = detail.reason ?? '';
			editing = true;
		} else if (e.key === 'u') {
			user = detail.target;
		}
	}

	$effect(() =>
		registerCommands([
			{
				id: 'cases:open',
				group: 'Templates',
				title: 'case #…',
				icon: 'cases',
				keywords: 'open case',
				template: 'case #',
				parse: (text) => {
					const m = /^(?:case )?#(\d+)$/i.exec(text);
					return m ? { label: `Open case #${m[1]}`, run: () => open(Number(m[1])) } : null;
				}
			}
		])
	);
</script>

<svelte:window {onkeydown} />

<div class="phead">
	<div>
		<h1>Cases</h1>
		<p>{page ? `${page.all} cases` : 'Loading…'}</p>
	</div>
	{#if modules && !modules.enabled.moderation}
		<a class="btn" href="/dashboard/{guildId}/modules"><Icon name="warn" />Moderation is off · manage modules</a>
	{/if}
</div>

<div class="toolbar">
	<div class="search">
		<Icon name="search" />
		<input class="inp" type="search" data-search aria-label="Search cases by number, user, moderator or reason" placeholder="Search #id, user, reason…" autocomplete="off" bind:value={q} />
	</div>
	<kbd class="hide-s">/</kbd>
	{#if page}<span class="cnt">{page.total} of {page.all}</span>{/if}
	<span style="flex:1"></span>
	<span class="dim hide-s" style="font-size:12px"><kbd>j</kbd><kbd>k</kbd> move · <kbd>e</kbd> edit reason · <kbd>u</kbd> filter user</span>
</div>

{#if page}
	<div class="facets">
		<div class="facet" role="group" aria-label="Filter by action">
			<span class="lbl">Action</span>
			{#each ACTIONS.filter((a) => page!.facets.action[a]) as a (a)}
				<button class="chip" aria-pressed={acts.includes(a)} onclick={() => (acts = toggle(acts, a))}>{a}<span class="n">{page.facets.action[a]}</span></button>
			{/each}
		</div>
		<div class="facet" role="group" aria-label="Filter by moderator">
			<span class="lbl">Moderator</span>
			{#each page.facets.moderator.slice(0, 8) as m (m.id)}
				<button class="chip" aria-pressed={mods.includes(m.id)} onclick={() => (mods = toggle(mods, m.id))}>
					<Avatar name={who(m)} src={m.avatar_url} size="xs" />{who(m)}<span class="n">{m.count}</span>
				</button>
			{/each}
			<button class="chip" aria-pressed={active} onclick={() => (active = !active)}>Active only</button>
			{#if user}
				<button class="chip" aria-pressed="true" aria-label="Remove user filter {who(user)}" onclick={() => (user = null)}>
					user: {who(user)} <Icon name="x" style="width:11px;height:11px" />
				</button>
			{/if}
			{#if filtered}<button class="btn sm ghost" onclick={clearFilters}>Clear all</button>{/if}
		</div>
	</div>
{/if}

<div class="split">
	<section class="pane" aria-label="Case list">
		{#if !page}
			<EmptyState>Loading…</EmptyState>
		{:else if !rows.length}
			<EmptyState icon="cases">
				{#if filtered}No cases match these filters. <button class="linkish" onclick={clearFilters}>Clear filters</button>
				{:else}No cases yet. They appear here as moderators act.{/if}
			</EmptyState>
		{:else}
			<DataTable
				label="Cases"
				{rows}
				key={(c) => String(c.number)}
				selected={selected === null ? null : String(selected)}
				onopen={(c) => open(c.number)}
				maxHeight="calc(100dvh - 300px)"
				columns={[
					{ key: 'number', label: 'Case' },
					{ key: 'action', label: 'Action' },
					{ key: 'target', label: 'User' },
					{ key: 'moderator', label: 'Moderator' },
					{ key: 'reason', label: 'Reason' },
					{ key: 'duration', label: 'Duration' },
					{ key: 'created', label: 'Created' },
					{ key: 'status', label: 'Status' }
				]}
			>
				{#snippet cell(c, col)}
					{#if col.key === 'number'}
						<button class="linkish mono" aria-label="Open case {c.number}" onclick={() => open(c.number)}>#{c.number}</button>
					{:else if col.key === 'action'}
						<span class="act" data-a={c.action}>{c.action}</span>
					{:else if col.key === 'target'}
						<span class="chname" style="font-weight:400"><Avatar name={who(c.target)} src={c.target.avatar_url} size="xs" />{who(c.target)}</span>
					{:else if col.key === 'moderator'}
						{who(c.moderator)}
					{:else if col.key === 'reason'}
						<span class="fg2" title={c.reason ?? ''} style="display:block;max-width:280px;overflow:hidden;text-overflow:ellipsis">{c.reason ?? 'No reason'}</span>
					{:else if col.key === 'duration'}
						<span class="mono">{duration(c.duration)}</span>
					{:else if col.key === 'created'}
						<span class="mono dim" title={new Date(c.created_at * 1000).toLocaleString()}>{ago(c.created_at * 1000)}</span>
					{:else if c.active === true}
						<span class="pill acc">active</span>
					{:else if c.active === false}
						<span class="dim mono" style="font-size:11.5px">inactive</span>
					{:else}
						<span class="dim">-</span>
					{/if}
				{/snippet}
			</DataTable>
			{#if page.next}
				<div class="pb" style="text-align:center">
					<button class="btn sm" disabled={more} onclick={loadMore}>{more ? 'Loading…' : `Load more · ${rows.length} of ${page.total}`}</button>
				</div>
			{/if}
		{/if}
	</section>

	<aside class="pane detail" class:open={sheet && !!detail} aria-label="Case detail">
		{#if detail}
			{@const w = detail.warnings}
			<div class="dhead">
				<span class="act" data-a={detail.action}>{detail.action}</span>
				<h2>Case #{detail.number}</h2>
				<span style="flex:1"></span>
				<button class="btn sm ghost" aria-label="Close case detail" onclick={close}><Icon name="x" /></button>
			</div>
			<div class="dsec" style="display:flex;gap:12px;align-items:center">
				<Avatar name={who(detail.target)} src={detail.target.avatar_url} size="lg" />
				<div>
					<div style="font-weight:600;font-size:15px">{who(detail.target)}</div>
					<div class="mono dim" style="font-size:11.5px">{detail.target.id}</div>
				</div>
			</div>
			<dl class="dgrid">
				<div><dt>Moderator</dt><dd>{who(detail.moderator)}</dd></div>
				<div><dt>Status</dt><dd>{detail.active === true ? 'active' : detail.active === false ? 'inactive' : 'n/a'}</dd></div>
				<div><dt>Created</dt><dd>{new Date(detail.created_at * 1000).toLocaleString()}</dd></div>
				<div><dt>Duration</dt><dd>{duration(detail.duration)}</dd></div>
				<div><dt>Expires</dt><dd>{detail.expires_at ? new Date(detail.expires_at * 1000).toLocaleString() : '-'}</dd></div>
				<div><dt>Case age</dt><dd>{ago(detail.created_at * 1000)}</dd></div>
			</dl>
			<div class="dsec">
				<h3>
					Reason
					{#if !editing}
						<button class="btn sm ghost" style="margin-left:auto" onclick={() => ((draft = detail!.reason ?? ''), (editing = true))}><Icon name="edit" />Edit <kbd>e</kbd></button>
					{/if}
				</h3>
				{#if editing}
					<form class="stack" style="gap:8px" onsubmit={saveReason}>
						<label class="sr" for="reasonIn">Reason for case {detail.number}</label>
						<textarea
							class="inp"
							id="reasonIn"
							rows="3"
							maxlength="512"
							bind:this={reasonInput}
							bind:value={draft}
							onkeydown={(e) => {
								if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) saveReason(e);
								if (e.key === 'Escape') editing = false;
							}}
						></textarea>
						<div class="rowline">
							<span class="hint">{MOD}↵ to save · esc to cancel</span>
							<span style="display:flex;gap:6px">
								<button type="button" class="btn sm" onclick={() => (editing = false)}>Cancel</button>
								<button class="btn sm pri" type="submit" disabled={!draft.trim()}>Save reason</button>
							</span>
						</div>
					</form>
				{:else}
					<p class="reason-t">{detail.reason ?? 'No reason provided'}</p>
				{/if}
			</div>
			<div class="dsec">
				<h3>Warnings</h3>
				<div class="warnbox">
					<b class:dim={!w}>{w}</b>
					<span style="flex:1;font-size:13px">active warning{w === 1 ? '' : 's'} for {who(detail.target)}</span>
					<button class="btn sm danger" disabled={!w} onclick={() => (confirmClear = true)}>Clear warnings</button>
				</div>
			</div>
			<div class="dsec">
				<h3>
					History for {who(detail.target)} <span class="cnt">{detail.history.length}</span>
					<button class="btn sm ghost" style="margin-left:auto" onclick={() => (user = detail!.target)}>Filter table <kbd>u</kbd></button>
				</h3>
				<ul class="hist">
					{#each detail.history as h (h.number)}
						<li>
							<button aria-current={h.number === detail.number} onclick={() => open(h.number, sheet)}>
								<span class="mono">#{h.number}</span><span class="act" data-a={h.action}>{h.action}</span>
								<span class="fg2" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{h.reason ?? 'No reason'}</span>
								<span class="mono dim">{ago(h.created_at * 1000)}</span>
							</button>
						</li>
					{/each}
				</ul>
			</div>
		{:else}
			<EmptyState>Select a case to see details.</EmptyState>
		{/if}
	</aside>
</div>

{#if sheet && detail}<div class="scrim sheet-scrim" onclick={close} aria-hidden="true"></div>{/if}

<Modal bind:open={confirmClear} title="Clear {detail?.warnings ?? 0} warning{detail?.warnings === 1 ? '' : 's'}?">
	<p>{detail ? who(detail.target) : ''}’s active warnings will be cleared, as <span class="mono">/clearwarns</span> does. The cases stay in their history.</p>
	{#snippet actions()}
		<button class="btn" onclick={() => (confirmClear = false)}>Cancel</button>
		<button class="btn danger" onclick={clearWarnings}>Clear warnings</button>
	{/snippet}
</Modal>
