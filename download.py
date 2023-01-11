import re
from math import ceil, floor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import urlretrieve as _urlretrieve

import click
import geopandas as gpd
import pint
import requests
from bs4 import BeautifulSoup
from shapely.geometry import box
from tqdm import tqdm

from geom import buffer
from grid import get_cells

ureg = pint.UnitRegistry()


@click.command()
@click.option(
    '--bbox',
    required=False,
    default=None,
    type=str,
    help='Bounding box to download data for. Should be west, south, east, north.'
)
@click.option(
    '--file',
    required=False,
    type=click.Path(
        exists=True, file_okay=True, dir_okay=False, resolve_path=True),
    default=None,
    help=
    'Geospatial file with geometry to download data for. Will download all image tiles that intersect this geometry. Must be a file format that GeoPandas can read.'
)
@click.option(
    '-b',
    '--buffer-dist',
    required=False,
    type=float,
    default=None,
    show_default=True,
    help='Buffer to use around provided geometry. Only used with --file argument.'
)
@click.option(
    '--buffer-unit',
    required=False,
    show_default=True,
    type=click.Choice(['mile', 'meter', 'kilometer'], case_sensitive=False),
    default='mile',
    help='Units for buffer.')
@click.option(
    '--buffer-projection',
    required=False,
    show_default=True,
    type=int,
    default=3488,
    help=
    'EPSG code for projection used when creating buffer. Coordinates must be in meters.'
)
@click.option(
    '--overwrite',
    is_flag=True,
    default=False,
    help="Re-download and overwrite existing files.")
def main(bbox, file, buffer_dist, buffer_unit, buffer_projection, overwrite):
    """Download FSTopo quads for given geometry
    """
    if (bbox is None) and (file is None):
        raise ValueError('Either bbox or file must be provided')

    if (bbox is not None) and (file is not None):
        raise ValueError('Either bbox or file must be provided')

    geometry = None
    if bbox:
        bbox = tuple(map(float, re.split(r'[, ]+', bbox)))
        geometry = box(*bbox)

    if file:
        gdf = gpd.read_file(file).to_crs(epsg=4326)

        # Create buffer if arg is provided
        if buffer_dist is not None:
            gdf = buffer(
                gdf,
                distance=buffer_dist,
                unit=buffer_unit,
                epsg=buffer_projection)

        geometry = gdf.unary_union

    if geometry is None:
        raise ValueError('Error while computing geometry')

    download_dir = Path('data/raw')
    download_dir.mkdir(parents=True, exist_ok=True)
    local_paths = download_fstopo(
        geometry, directory=download_dir, overwrite=overwrite)
    with open('paths.txt', 'w') as f:
        f.writelines(_paths_to_str(local_paths))


def download_fstopo(geometry, directory, overwrite):
    """Download FSTopo quads

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

    Args:
        - geometry: any shapely object; used to find intersection of FSTopo quads
        - directory (pathlib.Path): directory to download files to
        - overwrite (bool): whether to re-download and overwrite existing files
    """
    cells = list(get_cells(geometry, cell_size=0.125))
    blocks_dict = create_blocks_dict(cells)
    urls = get_urls(blocks_dict)

    local_paths = []
    counter = 1
    for url in urls:
        print(f'Downloading file {counter} of {len(urls)}')
        print(url)
        local_path = download_url(url, directory, overwrite=overwrite)
        if local_path is not None:
            local_paths.append(local_path)

        counter += 1

    return local_paths


def create_blocks_dict(cells):
    """
    The FS website directory goes by lat/lon boxes, so I need to get the
    whole-degree boxes

    FS uses the min for lat, max for lon, aka 46121 has quads with lat >= 46
    and lon <= -121
    """
    blocks_dict = {}
    for cell in cells:
        miny, maxx = cell.bounds[1:3]
        degree_y = str(floor(miny))
        degree_x = str(abs(ceil(maxx)))

        decimal_y = abs(miny) % 1
        minute_y = str(
            floor((decimal_y * ureg.degree).to(ureg.arcminute).magnitude))
        # Left pad to two digits
        minute_y = minute_y.zfill(2)

        # Needs to be abs because otherwise the mod of a negative number is
        # opposite of what I want.
        decimal_x = abs(maxx) % 1
        minute_x = str(
            floor((decimal_x * ureg.degree).to(ureg.arcminute).magnitude))
        # Left pad to two digits
        minute_x = minute_x.zfill(2)

        degree_block = degree_y + degree_x
        minute_block = degree_y + minute_y + degree_x + minute_x

        blocks_dict[degree_block] = blocks_dict.get(degree_block, [])
        blocks_dict[degree_block].append(minute_block)

    return blocks_dict


def get_urls(blocks_dict):
    """Find urls for FS Topo tif files given block locations

    Args:
        - blocks_dict: {header: [all_values]}, e.g. {'41123': ['413012322']}

    Returns:
        List[str]: urls to download
    """
    all_tif_urls = []
    for degree_block_id, minute_quad_ids in blocks_dict.items():
        block_url = 'https://data.fs.usda.gov/geodata/rastergateway/'
        block_url += 'states-regions/quad-index.php?'
        block_url += f'blockID={degree_block_id}'
        r = requests.get(block_url)
        soup = BeautifulSoup(r.content, 'lxml')
        links = soup.select('#skipheader li a')
        
        # Not sure what happens if the blockID page doesn't exist on the FS
        # website. Apparently internal server error from trying 99999
        if links:
            # Keep only quads that were found to be near trail
            links = [link for link in links if re.sub(".*_([0-9]{9})_FSTopo.tif*", r"\1", link.text) in minute_quad_ids]
            urls = [urljoin(block_url, link.get('href')) for link in links]
            tif_urls = [url for url in urls if url[-5:] == '.tiff']
            all_tif_urls.extend(tif_urls)

    return all_tif_urls


def download_url(url, directory, overwrite=False):
    # Cache original download in self.raw_dir
    parsed_url = urlparse(url)
    filename = Path(parsed_url.path).name
    local_path = Path(directory) / filename
    if overwrite or (not local_path.exists()):
        try:
            urlretrieve(url, local_path)
        except HTTPError:
            print(f'File could not be downloaded:\n{url}')
            return None

    return local_path.resolve()


def _paths_to_str(paths):
    return [str(path) for path in paths]


class DownloadProgressBar(tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


def urlretrieve(url, output_path):
    with DownloadProgressBar(unit='B', unit_scale=True, miniters=1,
                             desc=url.split('/')[-1]) as t:
        _urlretrieve(url, filename=output_path, reporthook=t.update_to)


if __name__ == '__main__':
    main()
