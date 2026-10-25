<script lang="ts">
	import Icon from './Icon.svelte';

	// Click (or Enter on the focused row) to edit; Enter or blur saves, Esc cancels.
	let {
		value,
		label,
		onsave,
		placeholder = 'default'
	}: { value: string; label: string; onsave: (v: string) => void; placeholder?: string } = $props();

	let editing = $state(false);
	let draft = $state('');

	function save() {
		if (!editing) return;
		editing = false;
		if (draft !== value) onsave(draft);
	}
</script>

{#if editing}
	<!-- svelte-ignore a11y_autofocus -->
	<input
		class="inp cell-in"
		aria-label={label}
		autofocus
		bind:value={draft}
		onblur={save}
		onkeydown={(e) => {
			e.stopPropagation();
			if (e.key === 'Enter') save();
			if (e.key === 'Escape') editing = false;
		}}
	/>
{:else}
	<button class="cell-ed" aria-label="Edit {label}" onclick={() => ((draft = value), (editing = true))}>
		{#if value}{value}{:else}<span class="dim">{placeholder}</span>{/if}
		<Icon name="edit" style="width:12px;height:12px;color:var(--fg3)" />
	</button>
{/if}
