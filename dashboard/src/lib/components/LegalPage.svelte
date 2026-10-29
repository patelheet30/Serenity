<script lang="ts">
	import Mark from './Mark.svelte';
	import SiteFooter from './SiteFooter.svelte';

	let { title, html }: { title: string; html: string } = $props();
</script>

<svelte:head>
	<title>{title} · Serenity</title>
	<meta name="description" content="Serenity's {title.toLowerCase()}." />
</svelte:head>

<header class="lnav">
	<a class="brand" href="/" aria-label="Serenity home"><Mark /><span class="wm">Serenity</span></a>
</header>

<main id="main" class="legal">
	<!-- Our own Markdown from src/lib/legal, rendered at build time. -->
	{@html html}
</main>

<SiteFooter />
