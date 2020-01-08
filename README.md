# FSTopo

Generate tiled map layer from US Forest Service Topo quads

### Source data

The USFS publishes 7.5' map quadrangles as TIF files. This repository downloads
quads for a given geometry and then stitches them together into raster map
tiles.


### Integration with `style.json`

The style JSON spec tells Mapbox GL how to style your map. Add the raster imagery
tiles as a source to overlay them with the other sources.

Within `sources`, each object key defines the name by which the later parts of
`style.json` should refer to the layer.

```json
"sources": {
  "fstopo": {
    "type": "raster",
    "url": "https://example.com/url/to/tile.json",
  	"tileSize": 512
  }
}
```

Where the `tile.json` for a raster layer should be something like:

```json
{
    "attribution": "<a href=\"https://www.fs.fed.us/\" target=\"_blank\">© USFS</a>",
    "description": "FSTopo quads",
    "format": "png",
    "id": "fstopo",
    "maxzoom": 14,
    "minzoom": 4,
    "name": "fstopo",
    "scheme": "tms",
    "tiles": ["https://example.com/url/to/tiles/{z}/{x}/{y}.png"],
    "version": "2.2.0"
}
```

Later in the style JSON, refer to the raster to style it. This example shows the
raster layer between zooms 11 and 15 (inclusive), and sets the opacity to 0.2 at
zoom 11 and 1 at zoom 15, with a gradual ramp in between.
```json
{
  "id": "fstopo",
  "type": "raster",
  "source": "fstopo",
  "minzoom": 11,
  "maxzoom": 15,
  "paint": {
    "raster-opacity": {
      "base": 1.5,
      "stops": [
        [
          11,
          0.2
        ],
        [
          15,
          1
        ]
      ]
    }
  }
}
```

## Installation

Clone the repository:

```
git clone https://github.com/nst-guide/fstopo
cd fstopo
```

This is written to work with Python >= 3.6. To install dependencies:

```
pip install -r requirements.txt
```

This also has dependencies on some C/C++ libraries. If you have issues
installing with pip, try Conda:
```
conda env create -f environment.yml
source activate fstopo
```

## Code Overview

#### `download.py`

```
> python download.py --help
Usage: download.py [OPTIONS]

  Download FSTopo quads for given geometry

Options:
  --bbox TEXT                     Bounding box to download data for. Should be
                                  west, south, east, north.
  --file FILE                     Geospatial file with geometry to download
                                  data for. Will download all image tiles that
                                  intersect this geometry. Must be a file
                                  format that GeoPandas can read.
  -b, --buffer-dist FLOAT         Buffer to use around provided geometry. Only
                                  used with --file argument.
  --buffer-unit [mile|meter|kilometer]
                                  Units for buffer.  [default: mile]
  --buffer-projection INTEGER     EPSG code for projection used when creating
                                  buffer. Coordinates must be in meters.
                                  [default: 3488]
  --overwrite                     Re-download and overwrite existing files.
  --help                          Show this message and exit.
```

Unlike map products served through USGS's National Map interface, there's no API
that I know of for Forest Service map quads. Instead, I find the 7.5' grid cells
that intersect the provided geometry, and then query the USFS website to see if
those TIFF files exist (because FSTopo maps are only created for forest service
lands).

FSTopo is a 7.5-minute latitude/longitude grid system. Forest service files
are grouped into 5-digit _blocks_, which correspond to the degree of
latitude and longitude. For example, [block 46121][block_46121] contains all
files that are between latitude 46° and 47° and longitude -121° to longitude
-122°. Within that, each latitude and longitude degree is split into 7.5'
segments. This means that there are 8 cells horizontally and 8 cells
vertically, for up to 64 total quads within each lat/lon block. FSTopo map
quads are only created for National Forest areas, so not every lat/lon block
has 64 files.

[block_46121]: https://data.fs.usda.gov/geodata/rastergateway/states-regions/quad-index.php?blockID=46121

The script then downloads each of these files to `data/raw/`. By default,
it doesn't re-download and overwrite a file that already exists. If you wish to
overwrite an existing file, use `--overwrite`.

#### `gdal`

Use `gdalbuildvrt` to generate a virtual dataset of all image tiles and
`gdal2tiles` to cut the output raster into map tiles.

`gdal2tiles.py` options:

-   `--processes`: number of individual processes to use for generating the base
    tiles. Change this to a suitable number for your computer.
-   I also use my forked copy of `gdal2tiles.py` in order to generate high-res retina tiles

## Usage

First, download desired DEM tiles, unzip them, build a VRT (Virtual Dataset),
and optionally download my fork of `gdal2tiles` which allows for creating
512x512 pngs.

```bash
# Download for some geometry
python download.py --file example.geojson

# Create virtual raster:
gdalbuildvrt -addalpha data/mosaic.vrt data/raw/*.tif

# Use gdal_translate to take the single band with color table and expand it
# into a 3-band VRT
gdal_translate -of vrt -expand rgb data/mosaic.vrt data/rgb.vrt

# Split the three rgb bands from rgb.vrt into separate files. This is
# because I need to merge these rgb bands with the transparency band that's
# the second band of mosaic.vrt from `gdalbuildvrt`, and I don't know how to
# do that without separating bands into individual VRTs and then merging
# them.
gdal_translate -b 1 data/rgb.vrt data/r.vrt
gdal_translate -b 2 data/rgb.vrt data/g.vrt
gdal_translate -b 3 data/rgb.vrt data/b.vrt
gdal_translate -b 2 data/mosaic.vrt data/a.vrt

# Merge the four bands back together
gdalbuildvrt -separate data/rgba.vrt data/r.vrt data/g.vrt data/b.vrt data/a.vrt

# Download my fork of gdal2tiles.py
# I use my own gdal2tiles.py fork for retina 2x 512x512 tiles
git clone https://github.com/nst-guide/gdal2tiles
cp gdal2tiles/gdal2tiles.py ./

# Any raster cell where the fourth band is 0 should be transparent. I
# couldn't figure out how to declare that all such data should be considered
# nodata, but from inspection it looks like those areas have rgb values of
# 54, 52, 52
# This process is still better than just declaring 54, 52, 52 to be nodata
# in a plain rgb file, in case there is any actual data in the map that's
# defined as this rgb trio
./gdal2tiles.py data/rgba.vrt data/fstopo_tiles --processes 16 --srcnodata="54,52,52,0" --exclude
```

### Compression

Running this for a 2 mile buffer for the entire Pacific Crest Trail, with
512x512 pixel tiles and a (`gdal2tiles` default) max zoom of 15 generated 9.7GB
of png files.

Note that these CLI commands worked on macOS, and some might need different
syntax on Linux.

#### WebP

```bash
cd data/fstopo_tiles
for f in */*/*.png; do mkdir -p ../fstopo_tiles_webp/$(dirname $f); cwebp $f -q 80 -o ../fstopo_tiles_webp/$f ; done
```

#### Lossy PNG

Because WebP images aren't supported on all devices, I also need to serve a PNG
layer. Unlike the [terrain-rgb elevation
layer](https://github.com/nst-guide/hillshade), for which lossy compression
would erase all meaning of the encoded elevation values, for regular images
lossy compression won't make much of a visual difference.

From limited searching, the best open source, command line PNG compressor seems
to be [`pngquant`](https://pngquant.org/). This creates PNG images that can be
displayed on any device. You can install with `brew install pngquant`.

In order to create a directory of compressed images:
```bash
# Make copy of png files
cp -r data/fstopo_tiles data/fstopo_tiles_png
cd data/fstopo_tiles_png
# Overwrite in place if it can be done without too much quality loss
find . -name '*.png' -print0 | xargs -0 -P8 -L1 pngquant -f --ext .png --quality=70-80 --skip-if-larger
```

Setting quality to `70-80` appears to create files that are about 25% of the
original size.

When run on Halfmile's PCT track data with a 2 mile buffer (including
alternates), the resulting data after pngquant compression has file sizes:
```
> du -csh * | sort -h
```

| Directory size | zoom level |
|----------------|------------|
| 8.0K           | 4          |
| 12K            | 5          |
| 48K            | 6          |
| 116K           | 7          |
| 468K           | 8          |
| 1.8M           | 9          |
| 7.2M           | 10         |
| 29M            | 11         |
| 108M           | 12         |
| 346M           | 13         |
| 914M           | 14         |
| 1.8G           | 15         |
| 3.2G           | total      |
