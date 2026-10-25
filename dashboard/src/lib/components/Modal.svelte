<script lang="ts">
	import type { Snippet } from 'svelte';

	// A native <dialog>: focus trapping, Esc and the backdrop come from the browser.
	let {
		open = $bindable(false),
		title,
		children,
		actions
	}: { open?: boolean; title: string; children: Snippet; actions?: Snippet } = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		if (open && !dialog?.open) dialog?.showModal();
		if (!open && dialog?.open) dialog.close();
	});
</script>

<dialog
	class="modal"
	aria-label={title}
	bind:this={dialog}
	onclose={() => (open = false)}
	onclick={(e) => e.target === dialog && (open = false)}
>
	<div class="sheet">
		<h2>{title}</h2>
		{@render children()}
		{#if actions}<div class="acts">{@render actions()}</div>{/if}
	</div>
</dialog>
