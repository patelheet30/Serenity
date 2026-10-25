<script lang="ts">
	import { goto } from '$app/navigation';
	import type { Me } from '../api';
	import { palette } from '../palette.svelte';
	import { logout } from '../session.svelte';
	import Avatar from './Avatar.svelte';
	import Icon from './Icon.svelte';

	let { me }: { me: Me } = $props();
	let open = $state(false);
	let root: HTMLElement | undefined = $state();
</script>

<svelte:window
	onclick={(e) => open && !root?.contains(e.target as Node) && (open = false)}
	onkeydown={(e) => open && e.key === 'Escape' && (open = false)}
/>

<div class="user" bind:this={root}>
	<button
		class="ubtn"
		aria-haspopup="menu"
		aria-expanded={open}
		aria-label="Account menu for {me.username}"
		onclick={() => (open = !open)}
	>
		<Avatar name={me.username} src={me.avatar_url} />
	</button>
	{#if open}
		<div class="umenu" role="menu">
			<div class="who">
				<b>{me.username}</b>
				<div class="dim mono" style="font-size:11.5px">{me.id}</div>
			</div>
			<button role="menuitem" onclick={() => ((open = false), goto('/servers'))}><Icon name="modules" />All servers</button>
			<button role="menuitem" onclick={() => ((open = false), (palette.keys = true))}><Icon name="key" />Keyboard shortcuts</button>
			<button role="menuitem" onclick={logout}><Icon name="logout" />Log out</button>
		</div>
	{/if}
</div>
