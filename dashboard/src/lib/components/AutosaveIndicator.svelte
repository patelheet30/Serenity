<script lang="ts">
	import { ago } from '../util';
	import Icon from './Icon.svelte';

	// Autosave is the settings pattern: each change saves on its own and this says when.
	let { status, savedAt }: { status: 'idle' | 'saving' | 'saved' | 'error'; savedAt?: number } =
		$props();

	let now = $state(Date.now());
	$effect(() => {
		const t = setInterval(() => (now = Date.now()), 15_000);
		return () => clearInterval(t);
	});
	const text = $derived.by(() => {
		now;
		if (status === 'saving') return 'Saving…';
		if (status === 'error') return 'Not saved';
		if (status === 'saved' && savedAt)
			return ago(savedAt) === 'now' ? 'Saved just now' : `Saved ${ago(savedAt)} ago`;
		return 'Autosave on';
	});
</script>

<span class="saved" role="status">
	<Icon name={status === 'error' ? 'warn' : 'check'} />
	<span class="txt" class:warn={status === 'error'}>{text}</span>
</span>

<style>
	.warn {
		color: var(--warn);
	}
</style>
