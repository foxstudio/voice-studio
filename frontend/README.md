# sv

Everything you need to build a Svelte project, powered by [`sv`](https://github.com/sveltejs/cli).

## Creating a project

If you're seeing this, you've probably already done this step. Congrats!

```sh
# create a new project
npx sv create my-app
```

To recreate this project with the same configuration:

```sh
# recreate this project
npx sv@0.15.4 create --template minimal --types ts --no-install .
```

## Developing

Once you've created a project and installed dependencies with `npm install` (or `pnpm install` or `yarn`), start a development server:

```sh
npm run dev

# or start the server and open the app in a new browser tab
npm run dev -- --open
```

## Building

To create a production version of your app:

```sh
npm run build
```

You can preview the production build with `npm run preview`.

> To deploy your app, you may need to install an [adapter](https://svelte.dev/docs/kit/adapters) for your target environment.

## Video localization timeline

`VideoCuttingTimeline.svelte` owns the measured viewport and observes container
resizes. Timeline time maps to viewport width × zoom, not the scrollable overflow
of labels. Resize, zoom, playback following and saved-position restoration use
that same coordinate space; hidden containers do not overwrite the saved position.
Mouse subtitle selection belongs to pointerdown; keyboard activation selects the
focused subtitle. The following click does not repeat pointer hit testing.

From the repository root, run `.venv/bin/python scripts/verify_timeline_viewport_web.py`
after a frontend build to verify selection, resize, scrolling and refresh with
isolated temporary data and fixed media providers.
