<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { IconName } from '../icons';
	import Icon from './Icon.svelte';

	let { icon, children }: { icon?: IconName; children: Snippet } = $props();
</script>

<div class="empty">
	{#if icon}<Icon name={icon} style="vertical-align:-3px;margin-right:6px" />{/if}
	{@render children()}
</div>
