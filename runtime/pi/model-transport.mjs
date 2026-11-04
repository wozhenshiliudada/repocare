import {createRequire} from 'node:module';

export function openModelTransport() {
  // Use pinned Pi's Undici instead of Node's shorter default connect deadline.
  const entry = import.meta.resolve('@earendil-works/pi-coding-agent');
  const {Agent, fetch} = createRequire(entry)('undici');
  const dispatcher = new Agent({connect: {timeout: 30_000}});
  return {
    fetch: (url, init) => fetch(url, {...init, dispatcher}),
    close: () => dispatcher.destroy(),
  };
}
