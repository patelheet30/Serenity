<script lang="ts">
	import { onMount } from 'svelte';

	// Informational, not a consent prompt: every cookie the site sets is needed to log in.
	const KEY = 'serenity_cookie_notice';
	let show = $state(false);

	onMount(() => {
		try {
			show = !localStorage.getItem(KEY);
		} catch {
			show = true; // storage blocked: show it, dismissing then lasts for this visit only
		}
	});

	function dismiss() {
		show = false;
		try {
			localStorage.setItem(KEY, '1');
		} catch {
			// nothing to remember it in
		}
	}
</script>

{#if show}
	<div class="cookie-note" role="region" aria-label="Cookie notice">
		<p>Serenity only uses the cookies needed to log you in. No tracking or analytics. <a href="/cookies" class="linkish">Cookie policy</a></p>
		<button class="btn sm" onclick={dismiss}>Got it</button>
	</div>
{/if}
