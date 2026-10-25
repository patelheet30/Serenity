<script lang="ts">
	import { dismiss, toasts } from '../toast.svelte';
	import Icon from './Icon.svelte';
</script>

<div id="toasts" role="status" aria-live="polite">
	{#each toasts as t (t.id)}
		<div class="toast" class:err={t.error}>
			<Icon name={t.error ? 'warn' : 'check'} />
			<span>{t.message}</span>
			{#if t.undo}
				<button
					class="btn sm"
					onclick={() => {
						t.undo?.();
						dismiss(t.id);
					}}><Icon name="undo" />Undo</button
				>
			{/if}
		</div>
	{/each}
</div>
