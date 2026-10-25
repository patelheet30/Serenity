<script lang="ts">
	let {
		checked = $bindable(false),
		label,
		disabled = false,
		onchange,
		onblocked
	}: {
		checked?: boolean;
		label: string;
		disabled?: boolean;
		onchange?: (v: boolean) => void;
		/** Clicked while disabled: say why. */
		onblocked?: () => void;
	} = $props();
</script>

<!-- aria-disabled (not disabled) so a blocked toggle can still be focused and explain itself. -->
<button
	class="tog"
	role="switch"
	aria-checked={checked}
	aria-label={label}
	aria-disabled={disabled}
	onclick={() => {
		if (disabled) return onblocked?.();
		checked = !checked;
		onchange?.(checked);
	}}
></button>
