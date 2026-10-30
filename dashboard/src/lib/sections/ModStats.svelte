<script lang="ts">
	import { api } from '../api';
	import Avatar from '../components/Avatar.svelte';
	import EmptyState from '../components/EmptyState.svelte';
	import Segmented from '../components/Segmented.svelte';
	import { ACTIONS as ACTION_META, GROUPS, type CaseUser } from './moderation';

	type Stats = {
		days: number;
		/** Exactly /modstats: case counts per action, plus `warnings` from the warnings table. */
		totals: Record<string, number>;
		active: { timeouts: number; warnings: number };
		daily: { day: number; counts: Record<string, number> }[];
		moderators: (CaseUser & { counts: Record<string, number> })[];
	};

	let { guildId, onerror }: { guildId: string; onerror: (e: unknown) => void } = $props();

	const ACTIONS = Object.keys(ACTION_META);
	const color = (a: string) => ACTION_META[a]?.color ?? 'var(--fg3)';
	const sum = (o: Record<string, number>) => Object.values(o).reduce((a, b) => a + b, 0);

	let days = $state(30);
	let stats = $state<Stats | null>(null);

	$effect(() => {
		const id = guildId;
		const d = days;
		let stale = false;
		stats = null; // never show one range's numbers under another's label
		api<Stats>(`/guilds/${id}/modstats?days=${d}`).then((s) => !stale && (stats = s), (e) => !stale && onerror(e));
		return () => (stale = true);
	});

	const W = 600;
	const H = 140;
</script>

<div class="phead">
	<div><h1>Mod stats</h1><p>Last {stats?.days ?? days} days · the same numbers as <span class="mono">/modstats</span></p></div>
	<Segmented label="Range" bind:value={days} options={[7, 30, 90].map((d) => ({ value: d, label: `${d}d` }))} />
</div>

{#if !stats}
	<section class="pane"><EmptyState>Loading…</EmptyState></section>
{:else}
	{@const { warnings: warningsIssued = 0, external = 0, ...byAction } = stats.totals}
	{@const cases = sum(byAction)}
	{@const perDay = stats.daily.map((d) => GROUPS.map((g) => g.a.reduce((n, a) => n + (d.counts[a] ?? 0), 0)))}
	{@const peak = Math.max(1, ...perDay.map((v) => v.reduce((a, b) => a + b, 0)))}
	{@const bw = W / perDay.length}
	{@const types = ACTIONS.map((a) => [a, byAction[a] ?? 0] as const).filter(([, n]) => n).sort((x, y) => y[1] - x[1])}
	{@const busiest = Math.max(1, ...stats.moderators.map((m) => sum(m.counts)))}
	<div class="tiles" style="margin-bottom:14px">
		<div class="tile"><b>{cases + warningsIssued}</b><span>Actions (cases + warnings, as /modstats)</span></div>
		<div class="tile"><b>{cases}</b><span>Cases{external ? ` · ${external} external` : ''}</span></div>
		<div class="tile"><b>{stats.active.timeouts}</b><span>Active timeouts now</span></div>
		<div class="tile"><b>{stats.active.warnings}</b><span>Active warnings now</span></div>
	</div>
	{#if !cases}
		<section class="pane"><EmptyState icon="stats">No moderation actions in the last {stats.days} days.</EmptyState></section>
	{:else}
		<div class="grid">
			<section class="pane s8" aria-labelledby="otT">
				<div class="ph">
					<h2 id="otT">Actions over time</h2>
					<span class="sp"></span>
					<div class="leg2">{#each GROUPS as g (g.n)}<span><i style:background={g.c}></i>{g.n}</span>{/each}</div>
				</div>
				<div class="pb">
					<svg viewBox="0 0 {W} {H + 18}" style="width:100%;height:auto" role="img" aria-label="Cases per day over {stats.days} days, peak {peak}">
						{#each [0, 0.5, 1] as f (f)}
							<line x1="0" x2={W} y1={H - f * H * 0.92} y2={H - f * H * 0.92} stroke="rgba(214,210,255,.07)" />
						{/each}
						{#each perDay as v, i (i)}
							{#each v as n, gi (gi)}
								{#if n}
									{@const below = v.slice(0, gi).reduce((a, b) => a + b, 0)}
									{@const h = (n / peak) * H * 0.92}
									<rect x={i * bw + bw * 0.15} y={H - (below / peak) * H * 0.92 - h} width={Math.max(1, bw * 0.7)} height={Math.max(1, h - 1)} rx="1.5" fill={GROUPS[gi].c}>
										<title>{new Date(stats.daily[i].day * 1000).toLocaleDateString()}: {n} {GROUPS[gi].n.toLowerCase()}</title>
									</rect>
								{/if}
							{/each}
						{/each}
						<text x="0" y={H + 14} fill="#8b87a8" font-size="10" font-family="Geist Mono, monospace">{stats.days}d ago</text>
						<text x={W} y={H + 14} fill="#8b87a8" font-size="10" text-anchor="end" font-family="Geist Mono, monospace">today</text>
					</svg>
				</div>
			</section>
			<section class="pane s4" aria-labelledby="btT">
				<div class="ph"><h2 id="btT">By type</h2></div>
				<div class="pb">
					{#each types as [a, n], i (a)}
						<div class="hb">
							<span class="act" data-a={a}>{a}</span>
							<span><span class="b" style:display="block" style:width="{(n / types[0][1]) * 100}%" style:background={color(a)} style:animation-delay="{i * 30}ms"></span></span>
							<span class="mono" style="text-align:right">{n}</span>
						</div>
					{/each}
				</div>
			</section>
			<section class="pane s12" aria-labelledby="bmT">
				<div class="ph">
					<h2 id="bmT">By moderator</h2>
					<span class="sp"></span>
					<div class="leg2">{#each ACTIONS as a (a)}<span><i style:background={color(a)}></i>{a}</span>{/each}</div>
				</div>
				<div class="scroll">
					<table class="tbl">
						<thead>
							<tr>
								<th>Moderator</th><th style="width:50%">Mix</th>
								{#each ACTIONS as a (a)}<th class="r hide-s">{a}</th>{/each}
								<th class="r">Total</th>
							</tr>
						</thead>
						<tbody>
							{#each stats.moderators as m (m.id)}
								{@const total = sum(m.counts)}
								<tr>
									<td><span class="chname"><Avatar name={m.name ?? m.id} src={m.avatar_url} />{m.name ?? m.id}</span></td>
									<td>
										<div class="stk" style:width="{(total / busiest) * 100}%">
											{#each ACTIONS.filter((a) => m.counts[a]) as a (a)}
												<i style:flex={m.counts[a]} style:background={color(a)} title="{a}: {m.counts[a]}"></i>
											{/each}
										</div>
									</td>
									{#each ACTIONS as a (a)}<td class="r mono hide-s">{#if m.counts[a]}{m.counts[a]}{:else}<span class="dim">0</span>{/if}</td>{/each}
									<td class="r mono">{total}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		</div>
	{/if}
{/if}
