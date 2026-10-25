<script lang="ts">
	let { values, label }: { values: number[]; label: string } = $props();

	const W = 160;
	const H = 28;
	const d = $derived.by(() => {
		const max = Math.max(1, ...values);
		const step = W / Math.max(1, values.length - 1);
		return values
			.map((v, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)} ${(H - 2 - (v / max) * (H - 4)).toFixed(1)}`)
			.join(' ');
	});
</script>

<svg class="spark" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label={label}>
	<path {d} fill="none" stroke="var(--acc)" stroke-width="1.4" vector-effect="non-scaling-stroke" />
</svg>
