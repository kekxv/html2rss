import * as esbuild from "npm:esbuild@0.20.2";
import { denoPlugins } from "jsr:@luca/esbuild-deno-loader@^0.11.1";
await esbuild.build({
  plugins: [...denoPlugins()],
  entryPoints: [
    "app.ts"
  ],
  outfile: "main.esm.js",
  bundle: true,
  format: "esm",
});
esbuild.stop();
