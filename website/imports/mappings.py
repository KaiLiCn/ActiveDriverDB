from collections import defaultdict
from os.path import basename

from genomic_mappings import make_snv_key, encode_csv
from helpers.bioinf import decode_mutation, DataInconsistencyError
from helpers.bioinf import is_sequence_broken
from helpers.parsers import read_from_gz_files
from helpers.bioinf import get_human_chromosomes
from helpers.bioinf import determine_strand
from flask import current_app
from database import bdb, bdb_refseq


def import_genome_proteome_mappings(
    proteins,
    mappings_dir='data/200616/all_variants/playground',
    mappings_file_pattern='annot_*.txt.gz',
    bdb_dir=''
):
    print('Importing mappings:')

    chromosomes = get_human_chromosomes()
    broken_seq = defaultdict(list)

    bdb.reset()
    bdb.close()

    path = current_app.config['BDB_DNA_TO_PROTEIN_PATH']
    if bdb_dir:
        path = bdb_dir + '/' + basename(path)

    bdb.open(path, cache_size=20480 * 8 * 8 * 8 *8)

    for line in read_from_gz_files(mappings_dir, mappings_file_pattern):
        try:
            chrom, pos, ref, alt, prot = line.rstrip().split('\t')
        except ValueError as e:
            print(e, line)
            continue

        assert chrom.startswith('chr')
        chrom = chrom[3:]

        assert chrom in chromosomes
        ref = ref.rstrip()

        # new Coding Sequence Variants to be added to those already
        # mapped from given `snv` (Single Nucleotide Variation)

        for dest in filter(bool, prot.split(',')):
            try:
                name, refseq, exon, cdna_mut, prot_mut = dest.split(':')
            except ValueError as e:
                print(e, line)
                continue
            assert refseq.startswith('NM_')
            # refseq = int(refseq[3:])
            # name and refseq are redundant with respect one to another

            assert exon.startswith('exon')
            exon = exon[4:]

            assert cdna_mut.startswith('c')
            try:
                cdna_ref, cdna_pos, cdna_alt = decode_mutation(cdna_mut)
            except ValueError as e:
                print(e, line)
                continue

            try:
                strand = determine_strand(ref, cdna_ref, alt, cdna_alt)
            except DataInconsistencyError as e:
                print(e, line)
                continue

            assert prot_mut.startswith('p')
            # we can check here if a given reference nuc is consistent
            # with the reference amino acid. For example cytosine in
            # reference implies that there should't be a methionine,
            # glutamic acid, lysine nor arginine. The same applies to
            # alternative nuc/aa and their combinations (having
            # references (nuc, aa): (G, K) and alt nuc C defines that
            # the alt aa has to be Asparagine (N) - no other is valid).
            # Note: it could be used to compress the data in memory too
            aa_ref, aa_pos, aa_alt = decode_mutation(prot_mut)

            try:
                # try to get it from cache (`proteins` dictionary)
                protein = proteins[refseq]
            except KeyError:
                continue

            assert aa_pos == (int(cdna_pos) - 1) // 3 + 1

            broken_sequence_tuple = is_sequence_broken(protein, aa_pos, aa_ref, aa_alt)

            if broken_sequence_tuple:
                broken_seq[refseq].append(broken_sequence_tuple)
                continue

            is_ptm_related = protein.has_sites_in_range(aa_pos - 7, aa_pos + 7)

            snv = make_snv_key(chrom, pos, cdna_ref, cdna_alt)

            # add new item, emulating set update
            item = encode_csv(
                strand,
                aa_ref,
                aa_alt,
                cdna_pos,
                exon,
                protein.id,
                is_ptm_related
            )

            bdb.add(snv, item)

    return broken_seq


def import_aminoacid_mutation_refseq_mappings(
    proteins,
    mappings_dir='data/200616/all_variants/playground',
    mappings_file_pattern='annot_*.txt.gz',
    bdb_dir=''
):
    print('Importing mappings:')

    chromosomes = get_human_chromosomes()

    bdb_refseq.reset()
    bdb_refseq.close()
    if bdb_dir:
        bdb_dir += '/'
    bdb_refseq.open(bdb_dir + basename(current_app.config['BDB_GENE_TO_ISOFORM_PATH']))

    for line in read_from_gz_files(mappings_dir, mappings_file_pattern):
        try:
            chrom, pos, ref, alt, prot = line.rstrip().split('\t')
        except ValueError:
            print('Import error: not enough values for "tab" split')
            print(line)
            continue

        assert chrom.startswith('chr')
        chrom = chrom[3:]

        assert chrom in chromosomes

        for dest in filter(bool, prot.split(',')):
            try:
                name, refseq, exon, cdna_mut, prot_mut = dest.split(':')
            except ValueError:
                print('Import error: not enough values for ":" split')
                print(line, dest)
                continue

            assert refseq.startswith('NM_')

            assert cdna_mut.startswith('c')
            cdna_ref, cdna_pos, cdna_alt = decode_mutation(cdna_mut)

            assert prot_mut.startswith('p')

            aa_ref, aa_pos, aa_alt = decode_mutation(prot_mut)

            try:
                # try to get it from cache (`proteins` dictionary)
                protein = proteins[refseq]
            except KeyError:
                continue

            assert aa_pos == (int(cdna_pos) - 1) // 3 + 1

            broken_sequence_tuple = is_sequence_broken(protein, aa_pos, aa_ref, aa_alt)

            if broken_sequence_tuple:
                continue

            key = protein.gene.name + ' ' + aa_ref + str(aa_pos) + aa_alt
            bdb_refseq.add(key, refseq)
