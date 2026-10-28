<script lang="ts">
	import { api } from '../../api';
	import Avatar from '../../components/Avatar.svelte';
	import EmptyState from '../../components/EmptyState.svelte';
	import Segmented from '../../components/Segmented.svelte';
	import { duration } from '../moderation';
	import type { TicketPageProps } from '../Tickets.svelte';
	import { who, type TicketUser } from './queue';

	type Times = { avg: number; median: number } | null;
	type Csat = { avg: number | null; count: number; distribution: number[]; response_rate: number | null };
	type Summary = {
		open: number;
		unclaimed: number;
		waiting_on_staff: number;
		opened: number;
		closed: number;
		auto_closed: number;
		escalated: number;
		first_response: { raw: Times; business: Times };
		time_to_close: Times;
		csat: Csat;
	};
	type Stats = Summary & {
		days: number;
		daily: { day: number; opened: number; closed: number }[];
		types: (Summary & { id: number; name: string; emoji: string | null; stages: { name: string; reached: number; avg_seconds: number | null }[] })[];
		tiers: { id: number; name: string; type: string; csat: Csat }[];
		staff: { user: TicketUser; claimed: number; handled: number; assisted: number; escalated_from: number; csat: { avg: number; count: number } | null }[];
	};

	let { guildId, staff, onerror }: TicketPageProps = $props();

	let days = $state(30);
	let stats = $state<Stats | null>(null);

	$effect(() => {
		const id = guildId;
		const d = days;
		let stale = false;
		stats = null; // never show one range's numbers under another's label
		api<Stats>(`/guilds/${id}/tickets/stats?days=${d}`).then((s) => !stale && (stats = s), (e) => !stale && onerror(e));
		return () => (stale = true);
	});

	const time = (t: Times) => (t ? duration(Math.round(t.median)) : '-');
	const pct = (n: number, of: number) => (of ? `${Math.round((n / of) * 100)}%` : '-');
	const stars = (c: { avg: number | null } | null) => (c?.avg != null ? `${c.avg.toFixed(1)}★` : '-');
	const rate = (c: Csat) => (c.response_rate == null ? '-' : `${Math.round(c.response_rate * 100)}%`);

	const W = 600;
	const H = 140;
</script>

<div class="toolbar">
	<Segmented label="Range" bind:value={days} options={[7, 30, 90].map((d) => ({ value: d, label: `${d}d` }))} />
	<span class="dim" style="font-size:12px">Times are medians. {staff ? 'Your types, and your own numbers.' : ''}</span>
</div>

{#if !stats}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	{@const s = stats}
	<div class="tiles" style="margin-bottom:14px">
		<div class="tile"><b>{s.open}</b><span>Open now · {s.unclaimed} unclaimed</span></div>
		<div class="tile"><b>{s.opened}</b><span>Opened · {s.closed} closed</span></div>
		<div class="tile"><b>{time(s.first_response.business)}</b><span>First response, support hours · {time(s.first_response.raw)} raw</span></div>
		<div class="tile"><b>{time(s.time_to_close)}</b><span>Time to close</span></div>
		<div class="tile"><b>{pct(s.escalated, s.opened)}</b><span>Escalated</span></div>
		<div class="tile"><b>{pct(s.auto_closed, s.closed)}</b><span>Auto-closed</span></div>
		<div class="tile"><b>{stars(s.csat)}</b><span>Average rating · {s.csat.count} rated</span></div>
		<div class="tile"><b>{rate(s.csat)}</b><span>Rating response rate</span></div>
	</div>
	{#if !s.opened && !s.closed && !s.open}
		<section class="pane"><EmptyState icon="tickets">No tickets in the last {s.days} days.</EmptyState></section>
	{:else}
		{@const peak = Math.max(1, ...s.daily.flatMap((d) => [d.opened, d.closed]))}
		{@const bw = W / s.daily.length}
		{@const most = Math.max(1, ...s.csat.distribution)}
		<div class="grid">
			<section class="pane s8" aria-labelledby="tvT">
				<div class="ph">
					<h2 id="tvT">Opened and closed</h2>
					<span class="sp"></span>
					<div class="leg2"><span><i style:background="var(--acc)"></i>opened</span><span><i style:background="var(--lilac)"></i>closed</span></div>
				</div>
				<div class="pb">
					<svg viewBox="0 0 {W} {H + 18}" style="width:100%;height:auto" role="img" aria-label="Tickets opened and closed per day over {s.days} days, peak {peak}">
						{#each [0, 0.5, 1] as f (f)}
							<line x1="0" x2={W} y1={H - f * H * 0.92} y2={H - f * H * 0.92} stroke="rgba(214,210,255,.07)" />
						{/each}
						{#each s.daily as d, i (d.day)}
							{#each [d.opened, d.closed] as n, k (k)}
								{#if n}
									{@const h = (n / peak) * H * 0.92}
									<rect x={i * bw + bw * (0.12 + k * 0.4)} y={H - h} width={Math.max(1, bw * 0.36)} height={h} rx="1.5" fill={k ? 'var(--lilac)' : 'var(--acc)'}>
										<title>{new Date(d.day * 1000).toLocaleDateString()}: {n} {k ? 'closed' : 'opened'}</title>
									</rect>
								{/if}
							{/each}
						{/each}
						<text x="0" y={H + 14} fill="#8b87a8" font-size="10" font-family="Geist Mono, monospace">{s.days}d ago</text>
						<text x={W} y={H + 14} fill="#8b87a8" font-size="10" text-anchor="end" font-family="Geist Mono, monospace">today</text>
					</svg>
				</div>
			</section>

			<section class="pane s4" aria-labelledby="rtT">
				<div class="ph"><h2 id="rtT">Ratings</h2><span class="sp"></span><span class="mono dim" style="font-size:12px">{stars(s.csat)}</span></div>
				<div class="pb">
					{#each [5, 4, 3, 2, 1] as r, i (r)}
						{@const n = s.csat.distribution[r - 1]}
						<div class="hb">
							<span class="mono">{r}★</span>
							<span><span class="b" style:display="block" style:width="{(n / most) * 100}%" style:background={r >= 4 ? 'var(--acc)' : r === 3 ? 'var(--fg2)' : 'var(--red)'} style:animation-delay="{i * 30}ms"></span></span>
							<span class="mono" style="text-align:right">{n}</span>
						</div>
					{/each}
				</div>
			</section>

			<section class="pane s12" aria-labelledby="byT">
				<div class="ph"><h2 id="byT">By type</h2></div>
				<div class="scroll">
					<table class="tbl">
						<thead>
							<tr>
								<th>Type</th><th class="r">Open</th><th class="r">Opened</th><th class="r">Closed</th>
								<th class="r">First response</th><th class="r hide-s">Raw</th><th class="r">To close</th>
								<th class="r hide-s">Escalated</th><th class="r hide-s">Auto-closed</th><th class="r">Rating</th>
							</tr>
						</thead>
						<tbody>
							{#each s.types as t (t.id)}
								<tr>
									<td>{t.emoji ?? ''} {t.name}</td>
									<td class="r mono">{t.open}</td>
									<td class="r mono">{t.opened}</td>
									<td class="r mono">{t.closed}</td>
									<td class="r mono">{time(t.first_response.business)}</td>
									<td class="r mono hide-s">{time(t.first_response.raw)}</td>
									<td class="r mono">{time(t.time_to_close)}</td>
									<td class="r mono hide-s">{pct(t.escalated, t.opened)}</td>
									<td class="r mono hide-s">{pct(t.auto_closed, t.closed)}</td>
									<td class="r mono">{stars(t.csat)} <span class="dim">{t.csat.count}</span></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>

			{#each s.types.filter((t) => t.stages.length) as t (t.id)}
				<section class="pane s6" aria-labelledby="stT{t.id}">
					<div class="ph"><h2 id="stT{t.id}">{t.name} stages</h2><span class="sp"></span><span class="dim" style="font-size:12px">of {t.opened} opened</span></div>
					<div class="scroll">
						<table class="tbl">
							<thead><tr><th>Stage</th><th style="width:40%">Reached</th><th class="r">%</th><th class="r">Time in stage</th></tr></thead>
							<tbody>
								{#each t.stages as st (st.name)}
									<tr>
										<td>{st.name}</td>
										<td><div class="stk" style:width="{t.opened ? (st.reached / t.opened) * 100 : 0}%"><i style:flex="1" style:background="var(--acc)" title="{st.reached} tickets"></i></div></td>
										<td class="r mono">{pct(st.reached, t.opened)}</td>
										<td class="r mono">{st.avg_seconds == null ? '-' : duration(Math.round(st.avg_seconds))}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			{/each}

			{#if s.tiers.length}
				<section class="pane s6" aria-labelledby="tiT">
					<div class="ph"><h2 id="tiT">Ratings by tier</h2><span class="sp"></span><span class="dim" style="font-size:12px">the tier a ticket closed with</span></div>
					<div class="scroll">
						<table class="tbl">
							<thead><tr><th>Tier</th><th class="r">Rating</th><th class="r">Rated</th><th class="r">Response</th></tr></thead>
							<tbody>
								{#each s.tiers as t (t.id)}
									<tr>
										<td>{t.name} <span class="dim">· {t.type}</span></td>
										<td class="r mono">{stars(t.csat)}</td>
										<td class="r mono">{t.csat.count}</td>
										<td class="r mono">{rate(t.csat)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			{/if}

			<section class="pane s12" aria-labelledby="sfT">
				<div class="ph">
					<h2 id="sfT">{staff ? 'You' : 'By staff'}</h2>
					<span class="sp"></span>
					<span class="dim hide-s" style="font-size:12px">handled: tickets closed with them credited · assisted: credited without claiming</span>
				</div>
				<div class="scroll">
					{#if s.staff.length}
						<table class="tbl">
							<thead>
								<tr><th>Staff</th><th class="r">Claimed</th><th class="r">Handled</th><th class="r">Assisted</th><th class="r">Escalated</th><th class="r">Rating</th></tr>
							</thead>
							<tbody>
								{#each s.staff as p (p.user.id)}
									<tr>
										<td><span class="chname" style="font-weight:400"><Avatar name={who(p.user)} src={p.user.avatar_url} size="xs" />{who(p.user)}</span></td>
										<td class="r mono">{p.claimed}</td>
										<td class="r mono">{p.handled}</td>
										<td class="r mono">{p.assisted}</td>
										<td class="r mono">{p.escalated_from}</td>
										<td class="r mono">{stars(p.csat)} <span class="dim">{p.csat?.count ?? 0}</span></td>
									</tr>
								{/each}
							</tbody>
						</table>
					{:else}
						<EmptyState>{staff ? 'You haven’t handled a ticket in this range.' : 'Nobody has handled a ticket in this range.'}</EmptyState>
					{/if}
				</div>
			</section>
		</div>
	{/if}
{/if}
