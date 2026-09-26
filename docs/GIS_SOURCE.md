# Kazakhstan map source

`frontend/public/kazakhstan-regions.json` is a local copy of `regions.json` from [`galymorg/new_qazaqstan_GeoJSON`](https://github.com/galymorg/new_qazaqstan_GeoJSON), MIT licence. It contains simplified 2024 SVG paths for all 17 regions and three cities of republican significance and is derived by its author from the `geokz` 2024 shapes.

The file's `KZ10`…`KZ79` codes are joined only after removing the `KZ` prefix and matching the verified two-digit KATO mapping in `apps/api/app/core/regions.py`. Unknown source codes are not guessed.
