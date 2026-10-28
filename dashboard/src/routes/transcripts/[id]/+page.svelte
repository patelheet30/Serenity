<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError, errorMessage, LOGIN_URL, send, type Me } from '#lib/api.ts';
	import { save } from '#lib/autosave.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import Mark from '#lib/components/Mark.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import UserMenu from '#lib/components/UserMenu.svelte';
	import { expiry, type Retention, type TicketUser } from '#lib/sections/tickets/transcripts.ts';
	import { session } from '#lib/session.svelte.ts';
	import { toast } from '#lib/toast.svelte.ts';

	type Message = {
		id: string;
		author: { id: string; name: string; avatar: string; color: number | null; bot: boolean };
		content: string;
		timestamp: number;
		edited: boolean;
		embeds: { title: string | null; description: string | null; fields: { name: string; value: string }[] }[];
		attachments: { filename: string; size: number; url: string; content_type: string | null }[];
	};
	type SysEvent = { kind: string; actor_id: string | null; data: Record<string, string>; created_at: number };
	type Transcript = Retention & {
		id: string;
		viewer: 'staff' | 'opener';
		guild: { id: string; name: string | null; icon_url: string | null };
		ticket: {
			number: number;
			type: { name: string; emoji?: string | null };
			status: 'open' | 'closed';
			opener_id: string;
			opened_at: number;
			closed_at: number | null;
			closed_by: string | null;
			reason: string | null;
		};
		fields: Record<string, string>;
		staff_fields: Record<string, string>;
		messages: Message[];
		events: SysEvent[];
		notes: { author_id: string; body: string; created_at: number }[];
		users: Record<string, TicketUser>;
		rating: { rating: number; comment: string | null } | null;
		can_rate: boolean;
		can_delete: boolean;
		is_opener: boolean;
	};

	const EVENTS: Record<string, (d: Record<string, string>, name: (id?: string) => string) => string> = {
		opened: () => 'opened the ticket',
		claimed: () => 'claimed it',
		assigned: (d, n) => `assigned it to ${n(d.assignee)}`,
		unclaimed: () => 'unclaimed it',
		member_added: (d, n) => `added ${n(d.user)}`,
		member_removed: (d, n) => `removed ${n(d.user)}`,
		stage: (d) => `moved it to ${d.to}`,
		priority: (d) => `set the priority to ${d.to}`,
		transferred: () => 'moved it to another type',
		escalated: (d) => `escalated it${d.reason ? `: ${d.reason}` : ''}`,
		deescalated: (d) => `de-escalated it${d.reason ? `: ${d.reason}` : ''}`,
		mod_action: (d) => `took a mod action (case #${d.case})`,
		fields_edited: () => 'edited the form answers',
		close_requested: () => 'asked to close it',
		close_declined: () => 'kept it open',
		closed: (d) => `closed it${d.reason ? `: ${d.reason}` : ''}`,
		auto_closed: () => 'auto-closed it',
		reopened: () => 'reopened it'
	};

	const id = $derived(page.params.id!);
	let me = $state<Me | null>(null);
	let t = $state<Transcript | null>(null);
	let error = $state<ApiError | null>(null);

	$effect(() => {
		const tid = id;
		let stale = false;
		t = null;
		error = null;
		(async () => {
			try {
				me = session.me ?? (session.me = await api<Me>('/me'));
				const r = await api<Transcript>(`/transcripts/${tid}`);
				if (!stale) t = r;
			} catch (e) {
				if (stale) return;
				// Logged out: log in and come straight back. Every transcript needs a session.
				if (e instanceof ApiError && e.status === 401) location.href = `${LOGIN_URL}?next=${encodeURIComponent(location.pathname)}`;
				else error = e instanceof ApiError ? e : new ApiError(0, 'network', errorMessage(e));
			}
		})();
		return () => (stale = true);
	});

	const names = $derived.by(() => {
		const out: Record<string, string> = {};
		for (const m of t?.messages ?? []) out[m.author.id] = m.author.name; // as they were then
		for (const [k, u] of Object.entries(t?.users ?? {})) if (u.name) out[k] = u.name;
		return out;
	});
	const name = (uid?: string | null) => (uid ? (names[uid] ?? uid) : 'Serenity');

	// Messages and system events in one timeline, by time.
	const timeline = $derived(
		t
			? [
					...t.messages.map((m) => ({ at: m.timestamp, m, e: null })),
					...t.events.map((e) => ({ at: e.created_at, m: null, e }))
				].sort((a, b) => a.at - b.at)
			: []
	);

	const when = (ts: number) => new Date(ts * 1000).toLocaleString();
	const color = (c: number | null) => (c ? `#${c.toString(16).padStart(6, '0')}` : undefined);
	// Discord's attachment links are signed with their expiry (hex unix seconds in `ex`).
	function expired(url: string) {
		const ex = new URL(url).searchParams.get('ex');
		return !!ex && parseInt(ex, 16) * 1000 < Date.now();
	}
	let broken = $state<Record<string, boolean>>({});

	let stars = $state(0);
	let comment = $state('');
	async function rate(e: SubmitEvent) {
		e.preventDefault();
		if (!t || !stars) return;
		const r = await save(() => send<Transcript['rating']>('POST', `/transcripts/${t!.id}/rating`, { rating: stars, comment }));
		if (!r || !t) return;
		t.rating = r;
		t.can_rate = false;
		toast(`Thanks! You rated this ticket ${r.rating}/5.`);
	}

	let confirm = $state<'delete' | 'request'>('delete');
	let asking = $state(false);
	async function act() {
		const what = confirm;
		asking = false;
		if (!t) return;
		const path = `/transcripts/${t.id}`;
		if (what === 'delete') {
			if ((await save(() => send('DELETE', path))) === undefined) return;
			t = null;
			error = new ApiError(404, 'deleted', 'This transcript was deleted.');
			return;
		}
		if ((await save(() => send('POST', `${path}/deletion-request`))) === undefined) return;
		t.deletion_requested_at = Math.floor(Date.now() / 1000);
		toast('Your request was sent to the server’s admins.');
	}
</script>

<svelte:head><title>{t ? `Ticket #${t.ticket.number} transcript` : 'Transcript'} · Serenity</title></svelte:head>

<header class="picker-top">
	<a class="brand" href="/" aria-label="Serenity home"><Mark /><span class="wm">Serenity</span></a>
	<span class="sp"></span>
	{#if me}<UserMenu {me} />{/if}
</header>

<main id="main" class="tx">
	{#if error}
		<section class="pane">
			<EmptyState icon={error.status === 404 ? 'lock' : 'warn'}>
				{error.status === 404 ? 'There’s no such transcript, or it isn’t yours to see.' : error.message}
				{#if error.code !== 'deleted' && error.status !== 404}<button class="linkish" onclick={() => location.reload()}>Try again</button>{/if}
			</EmptyState>
		</section>
	{:else if !t}
		<section class="pane"><EmptyState>Loading…</EmptyState></section>
	{:else}
		{@const tk = t.ticket}
		<div class="phead">
			<div style="display:flex;gap:12px;align-items:center">
				<Avatar kind="guild" name={t.guild.name ?? 'Server'} src={t.guild.icon_url} />
				<div>
					<h1>Ticket #{tk.number} · {tk.type.emoji ?? ''} {tk.type.name}</h1>
					<p>
						{t.guild.name ?? 'A server Serenity has left'} · opened by {name(tk.opener_id)} {when(tk.opened_at)}
						{#if tk.closed_at}· closed by {name(tk.closed_by)} {when(tk.closed_at)}{/if}
					</p>
				</div>
			</div>
			<div style="display:flex;gap:8px">
				{#if t.can_delete}<button class="btn danger" onclick={() => ((confirm = 'delete'), (asking = true))}>Delete transcript</button>{/if}
				{#if t.is_opener && !t.deletion_requested_at}<button class="btn" onclick={() => ((confirm = 'request'), (asking = true))}>Ask to delete it</button>{/if}
			</div>
		</div>

		<section class="pane">
			<dl class="dgrid" style="border-bottom:0">
				<div><dt>Kept</dt><dd class:acc={!!t.held_by_case}>{expiry(t)}{t.expires_at && !t.held_by_case ? ` (${new Date(t.expires_at * 1000).toLocaleDateString()})` : ''}</dd></div>
				<div><dt>Who can see it</dt><dd>{t.is_opener ? 'You' : name(tk.opener_id)} and {tk.type.name} staff{t.viewer === 'staff' ? ' (notes: staff only)' : ''}</dd></div>
				{#if tk.reason}<div><dt>Close reason</dt><dd>{tk.reason}</dd></div>{/if}
				{#if t.deletion_requested_at}<div><dt>Deletion</dt><dd class="warn-t">{t.is_opener ? 'You asked' : 'The opener asked'} for it to be deleted {when(t.deletion_requested_at)}</dd></div>{/if}
				{#if t.rating}<div><dt>Rating</dt><dd>{'★'.repeat(t.rating.rating)}<span class="dim">{'★'.repeat(5 - t.rating.rating)}</span>{t.rating.comment ? ` “${t.rating.comment}”` : ''}</dd></div>{/if}
			</dl>
		</section>

		{#if t.can_rate}
			<form class="pane pb stack rate" onsubmit={rate} aria-label="Rate this ticket">
				<h2>How did we do?</h2>
				<div class="stars" role="radiogroup" aria-label="Rating from 1 (poor) to 5 (great)">
					{#each [1, 2, 3, 4, 5] as n (n)}
						<button type="button" role="radio" aria-checked={stars === n} aria-label="{n} out of 5" class:on={n <= stars} onclick={() => (stars = n)}>★</button>
					{/each}
				</div>
				<label class="sr" for="rateComment">Anything to add? (optional)</label>
				<textarea class="inp" id="rateComment" rows="2" maxlength="500" placeholder="Anything to add? (optional)" bind:value={comment}></textarea>
				<div><button class="btn pri" type="submit" disabled={!stars}>Send rating</button></div>
			</form>
		{/if}

		{#if Object.keys(t.fields).length || Object.keys(t.staff_fields).length}
			<section class="pane">
				<div class="ph"><h2>Form answers</h2></div>
				<dl class="dgrid" style="border-bottom:0">
					{#each Object.entries(t.fields) as [label, answer] (label)}
						<div><dt>{label}</dt><dd class="txt">{answer}</dd></div>
					{/each}
					{#each Object.entries(t.staff_fields) as [label, answer] (label)}
						<div><dt>{label} <span class="sbadge">Staff only</span></dt><dd class="txt">{answer}</dd></div>
					{/each}
				</dl>
			</section>
		{/if}

		<section class="pane" aria-label="Messages">
			<div class="ph"><h2>Messages</h2><span class="cnt">{t.messages.length}</span></div>
			{#if !timeline.length}<EmptyState>No messages.</EmptyState>{/if}
			<ol class="log">
				{#each timeline as item, i (item.m ? `m${item.m.id}` : `e${i}`)}
					{#if item.m}
						{@const m = item.m}
						<li class="msg">
							<Avatar name={m.author.name} src={m.author.avatar || null} />
							<div style="min-width:0">
								<div class="hd">
									<b style:color={color(m.author.color)}>{m.author.name}</b>
									{#if m.author.bot}<span class="sbadge">Bot</span>{/if}
									<time class="mono dim" datetime={new Date(m.timestamp * 1000).toISOString()}>{when(m.timestamp)}</time>
									{#if m.edited}<span class="dim">(edited)</span>{/if}
								</div>
								{#if m.content}<p class="txt">{m.content}</p>{/if}
								{#each m.embeds as e, j (j)}
									<div class="embed">
										{#if e.title}<b>{e.title}</b>{/if}
										{#if e.description}<p class="txt">{e.description}</p>{/if}
										{#each e.fields as f, k (k)}<div><span class="dim">{f.name}</span><p class="txt">{f.value}</p></div>{/each}
									</div>
								{/each}
								{#each m.attachments as a (a.url)}
									{#if expired(a.url) || broken[a.url]}
										<div class="att dim"><Icon name="lock" />{a.filename} · expired: Discord no longer serves this file</div>
									{:else if a.content_type?.startsWith('image/')}
										<a href={a.url} target="_blank" rel="noopener"><img class="img" src={a.url} alt={a.filename} loading="lazy" onerror={() => (broken[a.url] = true)} /></a>
									{:else}
										<a class="att" href={a.url} target="_blank" rel="noopener"><Icon name="ext" />{a.filename} <span class="dim mono">{Math.ceil(a.size / 1024)} KB</span></a>
									{/if}
								{/each}
							</div>
						</li>
					{:else if item.e}
						{@const e = item.e}
						<li class="ev"><span class="dim mono">{when(e.created_at)}</span> <b>{name(e.actor_id)}</b> {(EVENTS[e.kind] ?? (() => e.kind.replace('_', ' ')))(e.data, name)}</li>
					{/if}
				{/each}
			</ol>
		</section>

		{#if t.viewer === 'staff'}
			<section class="pane" aria-label="Staff notes">
				<div class="ph"><h2>Staff notes</h2><span class="sbadge">Never shown to the opener</span></div>
				{#if !t.notes.length}<EmptyState>No staff notes.</EmptyState>{/if}
				<ol class="log">
					{#each t.notes as n, i (i)}
						<li class="msg">
							<Avatar name={name(n.author_id)} src={t.users[n.author_id]?.avatar_url ?? null} />
							<div>
								<div class="hd"><b>{name(n.author_id)}</b> <span class="mono dim">{when(n.created_at)}</span></div>
								<p class="txt">{n.body}</p>
							</div>
						</li>
					{/each}
				</ol>
			</section>
		{/if}
	{/if}
</main>

<Modal bind:open={asking} title={confirm === 'delete' ? 'Delete this transcript?' : 'Ask to delete this transcript?'}>
	<p>
		{#if confirm === 'delete'}It’s deleted for good, with its staff notes, as <span class="mono">/ticket transcript delete</span> does.
		{:else}The server’s admins are told you’d like it deleted; they decide.{/if}
	</p>
	{#snippet actions()}
		<button class="btn" onclick={() => (asking = false)}>Cancel</button>
		<button class="btn {confirm === 'delete' ? 'danger' : 'pri'}" onclick={act}>{confirm === 'delete' ? 'Delete' : 'Ask to delete'}</button>
	{/snippet}
</Modal>

<style>
	.tx { max-width: 880px; margin: 0 auto; padding: 8px 16px 80px; display: grid; gap: 14px; }
	.phead { margin-bottom: 0; }
	.phead h1 { font-size: 22px; }
	.txt { margin: 2px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
	.log { list-style: none; margin: 0; padding: 6px 0; }
	.msg { display: grid; grid-template-columns: 24px 1fr; gap: 10px; padding: 8px 14px; }
	.hd { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 13px; }
	.hd time, .hd .mono { font-size: 11px; }
	.ev { padding: 4px 14px 4px 48px; font-size: 12.5px; color: var(--fg2); }
	.ev .mono { font-size: 11px; margin-right: 4px; }
	.embed { margin-top: 6px; padding: 8px 10px; border-left: 3px solid var(--line2); border-radius: var(--r); background: var(--ink3); font-size: 13px; display: grid; gap: 4px; }
	.att { display: flex; width: fit-content; gap: 6px; align-items: center; margin-top: 6px; font-size: 12.5px; }
	.img { display: block; margin-top: 6px; max-width: min(100%, 360px); max-height: 280px; border-radius: var(--r); }
	.rate h2 { margin: 0; font-size: 15px; }
	.stars button { font-size: 26px; line-height: 1; padding: 0 2px; background: none; border: 0; color: var(--fg3); }
	.stars button.on { color: var(--acc); }
</style>
