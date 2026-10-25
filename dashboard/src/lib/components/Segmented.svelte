<script lang="ts" generics="T extends string | number">
	let {
		value = $bindable(),
		options,
		label,
		onchange
	}: {
		value: T;
		options: { value: T; label: string }[];
		label: string;
		/** Called after `value` changes, with the value it had before (to revert a failed save). */
		onchange?: (v: T, previous: T) => void;
	} = $props();
</script>

<div class="seg" role="group" aria-label={label}>
	{#each options as o (o.value)}
		<button aria-pressed={value === o.value} onclick={() => {
				if (o.value === value) return;
				const previous = value;
				value = o.value;
				onchange?.(o.value, previous);
			}}>{o.label}</button>
	{/each}
</div>
