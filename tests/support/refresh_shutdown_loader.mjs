// Register an asynchronous loader so it composes with tsx on Node 22 and 24.
// Only the subprocess lifecycle tests import this preloader.
import { register } from 'node:module';

register('./refresh_shutdown_hooks.mjs', import.meta.url);
