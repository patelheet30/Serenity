<script lang="ts">
	// Optional threshold: a dashed line, and the line turns amber and its end glows while the
	// latest value is over it. `muted` greys it out (e.g. a paused channel).
	let {
		values,
		label,
		threshold,
		muted = false
	}: { values: number[]; label: string; threshold?: number; muted?: boolean } = $props();

	const W = 160;
	const H = 28;
	const max = $derived(Math.max(1, (threshold ?? 0) * 1.3, ...values));
	const y = (v: number) => H - 2 - (v / max) * (H - 4);
	const pts = $derived(values.map((v, i) => [(i * W) / Math.max(1, values.length - 1), y(v)]));
	const d = $derived(pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`).join(' '));
	const over = $derived(threshold !== undefined && (values.at(-1) ?? 0) > threshold);
	const color = $derived(muted ? 'var(--fg3)' : over ? 'var(--amber)' : 'var(--acc)');
	const last = $derived(pts.at(-1));
</script>

<svg class="spark" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label={label}>
	{#if threshold !== undefined}
		<line x1="0" x2={W} y1={y(threshold)} y2={y(threshold)} stroke="rgba(236,226,208,.16)" stroke-dasharray="2 3" vector-effect="non-scaling-stroke" />
	{/if}
	<path {d} fill="none" stroke={color} stroke-width="1.4" vector-effect="non-scaling-stroke" />
	{#if last && threshold !== undefined}
		<circle cx={last[0] - 1.5} cy={last[1]} r="2.2" fill={color} class:glow={!muted} class:over />
	{/if}
</svg>

<style>
	.glow {
		filter: drop-shadow(0 0 4px #8fe3bf);
	}
	.glow.over {
		filter: drop-shadow(0 0 4px #f0b46a);
	}
</style>
