import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

let entry;
try {
  entry = createRequire(`${process.cwd()}/`).resolve("eslint");
} catch (error) {
  if (error.code !== "MODULE_NOT_FOUND") throw error;
  console.log("[]");
  process.exit(0);
}
const { ESLint } = await import(pathToFileURL(entry).href);
const eslint = new ESLint({ overrideConfig: { files: ["**/*.{ts,tsx,mts,cts}"] } });
const files = process.argv.slice(2);
const ignored = await Promise.all(files.map((file) => eslint.isPathIgnored(file)));
console.log(JSON.stringify(files.filter((_, index) => ignored[index])));
