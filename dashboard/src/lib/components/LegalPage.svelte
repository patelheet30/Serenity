<script lang="ts">
	import Mark from './Mark.svelte';
	import SiteMeta from './SiteMeta.svelte';
	import SiteFooter from './SiteFooter.svelte';

	let { title, description, html, path }: { title: string; description: string; html: string; path: string } = $props();
</script>

<SiteMeta title="{title} · Serenity" {description} {path} />

<header class="lnav">
	<a class="brand" href="/" aria-label="Serenity home"><Mark /><span class="wm">Serenity</span></a>
</header>

<main id="main" class="legal">
	<!-- Our own Markdown from src/lib/legal, rendered at build time. -->
	{@html html}
</main>

<SiteFooter />
