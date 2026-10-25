<script lang="ts">
	import { onMount } from 'svelte';
	import { api, inviteUrl, LOGIN_URL, type Me } from '#lib/api.ts';
	import Icon from '#lib/components/Icon.svelte';
	import Mark from '#lib/components/Mark.svelte';
	import { MOD } from '#lib/util.ts';

	const SITE = import.meta.env.VITE_SITE_URL ?? '';
	const TITLE = 'Serenity: slowmode that breathes with your chat';
	const DESCRIPTION =
		'A Discord bot that eases slowmode up and down on its own as each channel gets busy or calms down. Audit logging and moderation cases included.';

	// Logged-in visitors get "Open dashboard" instead of the login button.
	let me: Me | null = $state(null);

	// --- live demo: #memes picks up, slowmode engages, the chat settles -----------------------------
	const W = 400;
	const H = 150;
	const y = (v: number) => H - 6 - (v / 40) * (H - 14);
	const fS = (s: number) => (!s ? 'off' : `${s}s`);
	const clock = () =>
		new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

	let data = $state(Array.from({ length: 60 }, (_, i) => 8 + Math.sin(i / 4) * 1.5 + Math.random() * 1.5));
	let threshold = $state(10);
	let slowmode = $state(0);
	let typed = $state('');
	let log: { t: string; k: string; text: string; cls: string }[] = $state([]);
	let paused = $state(false);

	const path = $derived(
		data.map((v, i) => `${i ? 'L' : 'M'}${((i * W) / (data.length - 1)).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
	);
	const last = $derived(y(data[data.length - 1]));

	function line(k: string, text: string, cls = 'acc') {
		log = [...log, { t: clock(), k, text, cls }].slice(-6);
	}

	onMount(() => {
		api<Me>('/me').then((m) => (me = m), () => {});

		line('auto', '#general 14.2/min over 10 · slowmode off → 5s · 81%');
		line('ok', '#art-share added to auto slowmode');

		if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
			data = data.map((v, i) =>
				i < 25 ? 8 + Math.random() * 2 : i < 38 ? 8 + (i - 25) * 2 + Math.random() * 2 : 34 - (i - 38) * 1.1 + Math.random()
			);
			threshold = 15;
			slowmode = 10;
			line('ok', '#memes threshold 10 → 15 msg/min');
			line('auto', '#memes 31.8/min over 15 · slowmode off → 10s · 88%');
			line('auto', '#memes settled at 11.4/min · slowmode 10s → 5s');
			return;
		}

		let tick = 0;
		let target = 8;
		let typing: { s: string; i: number } | null = null;
		const type = (s: string) => (typing = { s, i: 0 });
		const script: Record<number, () => void> = {
			5: () => type('set #memes threshold 15'),
			34: () => ((typed = ''), (threshold = 15), line('ok', '#memes threshold 10 → 15 msg/min')),
			44: () => ((target = 33), line('live', '#memes picking up, velocity +62%', 'dim')),
			82: () => ((slowmode = 10), (target = 12), line('auto', '#memes 31.8/min over 15 · slowmode off → 10s · 88%')),
			130: () => ((slowmode = 5), (target = 9), line('auto', '#memes settled at 11.4/min · slowmode 10s → 5s')),
			146: () => type('ignore #bots for message'),
			176: () => ((typed = ''), line('ok', '#bots ignored for message logs')),
			190: () => type('case 1042'),
			204: () => ((typed = ''), line('case', '#1042 ban vapor.wav by Aria · phishing links', 'fg2')),
			236: () => ((slowmode = 0), line('auto', '#memes calm · slowmode 5s → off')),
			270: () => ((tick = -1), (threshold = 10), (log = []))
		};

		const timer = setInterval(() => {
			if (paused) return;
			tick++;
			if (typing) {
				typing.i++;
				typed = typing.s.slice(0, typing.i);
				if (typing.i >= typing.s.length) typing = null;
			}
			script[tick]?.();
			if (tick % 2 === 0) {
				const cur = data[data.length - 1];
				const damp = slowmode ? 0.82 : 1;
				const next = Math.min(39, Math.max(1, cur + (target * damp - cur) * 0.12 + (Math.random() - 0.5) * 2.4));
				data = [...data.slice(1), next];
			}
		}, 100);
		return () => clearInterval(timer);
	});
</script>

<svelte:head>
	<title>{TITLE}</title>
	<meta name="description" content={DESCRIPTION} />
	<meta property="og:type" content="website" />
	<meta property="og:title" content={TITLE} />
	<meta property="og:description" content={DESCRIPTION} />
	<meta property="og:image" content="{SITE}/og.png" />
	<meta name="twitter:card" content="summary_large_image" />
</svelte:head>

<header class="lnav">
	<a class="brand" href="/" aria-label="Serenity home"><Mark /><span class="wm">Serenity</span></a>
	<span class="sp"></span>
	<a class="btn ghost hide-s" href="#how">How it decides</a>
	{#if me}
		<a class="btn" href="/servers">Open dashboard</a>
	{:else}
		<a class="btn" href={LOGIN_URL} data-sveltekit-reload>Login with Discord</a>
	{/if}
</header>

<main id="main">
	<section class="hero">
		<div>
			<h1>Slowmode that <em>breathes</em> with your chat.</h1>
			<p>
				Serenity watches each channel’s pace and eases slowmode up and down on its own. Logging
				and moderation cases included.
			</p>
			<div class="ctas">
				<a class="btn pri lg" href={inviteUrl()} data-sveltekit-reload>Add to Discord <Icon name="arrow" /></a>
				{#if me}
					<a class="btn lg" href="/servers">Open dashboard</a>
				{:else}
					<a class="btn lg" href={LOGIN_URL} data-sveltekit-reload>Login with Discord</a>
				{/if}
			</div>
		</div>
		<div class="demo" role="figure" aria-label="Live demo: the command palette driving Serenity">
			<div class="demo-in">
				<kbd>{MOD}K</kbd>
				<span class="q" style="flex:1"><span>{typed}</span><span class="caret" aria-hidden="true"></span></span>
				<button
					class="btn sm ghost"
					aria-pressed={paused}
					aria-label={paused ? 'Play demo' : 'Pause demo'}
					onclick={() => (paused = !paused)}>{paused ? 'Play' : 'Pause'}</button
				>
			</div>
			<div class="demo-chart">
				<div class="meta"><span>#memes · msgs/min</span><span>slowmode <b class="acc">{fS(slowmode)}</b></span></div>
				<svg
					viewBox="0 0 {W} {H}"
					preserveAspectRatio="none"
					role="img"
					aria-label="Message rate in #memes rising, then calming after slowmode engages"
				>
					<defs>
						<linearGradient id="dg" x1="0" x2="0" y1="0" y2="1">
							<stop offset="0" stop-color="#8fe3bf" stop-opacity=".22" />
							<stop offset="1" stop-color="#8fe3bf" stop-opacity="0" />
						</linearGradient>
					</defs>
					<line x1="0" x2={W} y1={y(threshold)} y2={y(threshold)} stroke="rgba(240,180,106,.6)" stroke-dasharray="3 4" vector-effect="non-scaling-stroke" />
					<text x={W - 4} y={y(threshold) - 5} fill="#f0b46a" font-size="10" text-anchor="end" font-family="Geist Mono, monospace">threshold {threshold}</text>
					<path d="{path} L{W} {H} L0 {H}Z" fill="url(#dg)" />
					<path d={path} fill="none" stroke="#8fe3bf" stroke-width="1.6" vector-effect="non-scaling-stroke" />
					<circle cx={W - 2} cy={last} r="3" fill="#8fe3bf" style="filter:drop-shadow(0 0 6px #8fe3bf)" />
				</svg>
			</div>
			<ol class="demo-log" aria-live="off">
				{#each log as l (l.t + l.text)}
					<li><span class="t">{l.t}</span><span class="k {l.cls}">{l.k}</span><span>{l.text}</span></li>
				{/each}
			</ol>
		</div>
	</section>

	<section class="lsec" id="how">
		<div class="how">
			<div>
				<h2>Four signals, one calm decision.</h2>
				<p class="lead">
					Every interval, each channel gets a score. Cross your threshold and slowmode steps up.
					Settle down and it steps back off. No one has to babysit #memes on a Friday night.
				</p>
			</div>
			<div>
				<div class="wbar" aria-hidden="true">
					<div style="flex:40;background:var(--acc)">40</div>
					<div style="flex:30;background:#6fb99a">30</div>
					<div style="flex:20;background:#4f8d74">20</div>
					<div style="flex:10;background:#3a6655;color:var(--fg)">10</div>
				</div>
				<div class="wlist">
					<div><b>40%</b><span>Current rate</span></div>
					<div><b>30%</b><span>Historical pattern</span></div>
					<div><b>20%</b><span>Velocity</span></div>
					<div><b>10%</b><span>Past effectiveness</span></div>
				</div>
			</div>
		</div>
	</section>

	<section class="lsec">
		<h2>The rest of the toolkit is just as quiet.</h2>
		<div class="feat">
			<article>
				<span class="k">logging</span>
				<div>
					<h3>Audit logs, routed</h3>
					<p>Member, message, voice, server and mod events posted as embeds to the channels you choose. Nothing is stored.</p>
				</div>
			</article>
			<article>
				<span class="k">moderation</span>
				<div>
					<h3>Cases with memory</h3>
					<p>Every ban, kick, timeout and warning becomes a numbered case with full per-user history.</p>
				</div>
			</article>
			<article>
				<span class="k">{MOD} K</span>
				<div>
					<h3>Every setting is one keystroke away</h3>
					<p>Type “ignore #bots for message” or “case 1042”. The dashboard does the rest.</p>
				</div>
			</article>
		</div>
	</section>

	<section class="lsec final">
		<h2 style="margin:0">Running in 75+ servers. Yours next.</h2>
		<a class="btn pri lg" href={inviteUrl()} data-sveltekit-reload>Add to Discord <Icon name="arrow" /></a>
	</section>
</main>

<footer class="lfoot">
	<span>Serenity</span>
	<a href="https://github.com/patelheet30/Serenity/blob/main/privacy.md" class="linkish">Privacy</a>
</footer>
