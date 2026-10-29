<script lang="ts">
	// What a shared link shows (Discord, Slack, X…): the public pages are prerendered, so these tags
	// are in the HTML a crawler fetches. Image URLs must be absolute.
	const SITE = (import.meta.env.VITE_SITE_URL || 'https://serenitybot.app').replace(/\/$/, '');

	let { title, description, path }: { title: string; description: string; path: string } = $props();
</script>

<svelte:head>
	<title>{title}</title>
	<meta name="description" content={description} />
	<link rel="canonical" href="{SITE}{path}" />
	<meta property="og:type" content="website" />
	<meta property="og:site_name" content="Serenity" />
	<meta property="og:title" content={title} />
	<meta property="og:description" content={description} />
	<meta property="og:url" content="{SITE}{path}" />
	<meta property="og:image" content="{SITE}/og.png" />
	<meta property="og:image:width" content="1200" />
	<meta property="og:image:height" content="630" />
	<meta property="og:image:alt" content="Serenity's orb mascot beside the line: Slowmode that breathes with your chat." />
	<meta name="twitter:card" content="summary_large_image" />
	<meta name="twitter:image" content="{SITE}/og.png" />
</svelte:head>
