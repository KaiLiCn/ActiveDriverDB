from collections import defaultdict

from pandas import read_table, Series, to_numeric, DataFrame
from tqdm import tqdm

from helpers.parsers import parse_fasta_file
import imports.protein_data as importers
from imports.sites.site_importer import SiteImporter


class HPRDImporter(SiteImporter):
    """Human Protein Reference Database site importer.

    To use this importer one need to download HPRD: http://hprd.org/download
    and place unpacked FLAT_FILES_072010 directory under data/sites/
    """

    requires = {importers.proteins_and_genes, importers.sequences}
    requires.update(SiteImporter.requires)

    source_name = 'HPRD'
    site_types = ['phosphorylation', 'glycosylation', 'acetylation']

    def __init__(
        self, sequences_path='PROTEIN_SEQUENCES.txt', mappings_path='HPRD_ID_MAPPINGS.txt',
        dir_path='data/sites/HPRD/FLAT_FILES_072010/'
    ):

        super().__init__()
        self.mappings = self.load_mappings(dir_path + mappings_path)
        self.sequences = self.load_sequences(dir_path + sequences_path)

    @staticmethod
    def load_sequences(path):
        sequences = defaultdict(str)

        def append(hprd_isoform_id, line):
            sequences[hprd_isoform_id] += line

        parse_fasta_file(path, append, on_header=lambda header: header.split('|')[1])
        return sequences

    def get_sequence_of_protein(self, site):
        return self.sequences[site.substrate_isoform_id]

    @staticmethod
    def load_mappings(mappings_path):
        header = [
            'hprd_id', 'geneSymbol', 'nucleotide_accession', 'protein_accession',
            'entrezgene_id', 'omim_id', 'swissprot_id', 'main_name'
        ]
        mappings = read_table(mappings_path, names=header).dropna(subset=['nucleotide_accession'])
        return mappings

    def add_nm_refseq_identifiers(self, sites: DataFrame):

        identifiers_subset = self.mappings[['nucleotide_accession', 'protein_accession']]

        sites = sites.merge(identifiers_subset, left_on='substrate_refseq_id', right_on='protein_accession')

        sites['nucleotide_accession'], _ = sites['nucleotide_accession'].str.split('.', 1).str

        sites.rename({'nucleotide_accession': 'refseq_nm'}, axis='columns', inplace=True)

        return sites

    def load_sites(self, path='data/sites/HPRD/FLAT_FILES_072010/POST_TRANSLATIONAL_MODIFICATIONS.txt', **filters):

        header = (
            'substrate_hprd_id', 'substrate_gene_symbol', 'substrate_isoform_id', 'substrate_refseq_id', 'site',
            'residue', 'enzyme_name', 'enzyme_hprd_id', 'modification_type', 'experiment_type', 'reference_id'
        )

        all_sites = read_table(path, names=header, converters={
            'site': lambda pos: pos.rstrip(';-'),
            'modification_type': str.lower,
            'reference_id': lambda ref: str(ref).split(',')
        })

        # only chosen site types
        sites = all_sites[all_sites.modification_type.isin(self.site_types)]

        # conversion of site position to numeric can be only performed after filtering out
        # PTM like bisulfide bounds (for which positions of both ends are separated by ';')
        sites['site'] = to_numeric(sites['site'])

        # map NP refseq to NM:
        sites = self.add_nm_refseq_identifiers(sites)

        normalized_names = {
            'refseq_nm': 'refseq',
            'site': 'position',
            'modification_type': 'mod_type',
            'enzyme_name': 'kinases'
        }

        sites.rename(normalized_names, axis='columns', inplace=True)

        mapped_sites = self.map_sites_to_isoforms(sites)

        columns_to_import = ['refseq', 'position', 'residue', 'mod_type', 'reference_id', 'kinases']
        mapped_sites = mapped_sites[columns_to_import]

        site_objects = []

        print('Creating database objects:')

        for site_data in tqdm(mapped_sites.itertuples(index=False), total=len(mapped_sites)):

            site, new = self.add_site(*site_data)

            if new:
                site_objects.append(site)

        return site_objects