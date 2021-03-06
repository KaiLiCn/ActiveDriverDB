from collections import defaultdict

from database import bdb_refseq
from database import get_or_create
from helpers.bioinf import decode_raw_mutation
from models import Mutation, Drug, Gene
from models import Protein
from search.mutation_result import SearchResult


def iterate_affected_isoforms(gene_name, ref, pos, alt):
    """Returns all isoforms where specified mutation might happen.

    Explanation: shouldn't we look for refseq with gene name only?

    Well, we have to look for all isoforms of given gene which
    cover given mutation - so those with length Y: X <= Y, where
    X is the position (pos) of analysed mutation.

    There is on more constraint: some proteomic mutations cannot
    be caused by a single genomic mutations: F => S cannot be a
    result of a single SNV/SNP because neither UUU nor UUC could be
    changed to AGU or AGC in a single step.

    There are many such isoforms and simple lookup:
        gene_name => preferred_isoform, or
        gene_name => all_isoforms
    is not enough to satisfy all conditions.

    So what do we have here is (roughly) an equivalent to:

        from models import Gene

        # the function below should check if we don't have symbols
        # that are not representing any of known amino acids.

        is_mut_allowed(alt)

        # function above and below were not implemented but lets
        # assume that they throw a flow-changing exception

        can_be_result_of_single_snv(ref, alt)

        gene = Gene.query.filter_by(name=gene_name).one()
        return [
            isoform
            for isoform in gene.isoforms
            if (isoform.length >= pos and
                isoform.sequence[pos - 1] == ref)
        ]
    """
    hash_key = gene_name + ' ' + ref + str(pos) + alt
    refseqs = bdb_refseq[hash_key]

    return Protein.query.filter(Protein.refseq.in_(refseqs))


def get_protein_muts(gene_name, mut):
    """Retrieve corresponding mutations from all isoforms

    associated with given gene which are correct (i.e. they do not
    lie outside the range of a protein isoform and have the same
    reference residues). To speed up the lookup we use precomputed
    berkleydb hashmap.
    """
    ref, pos, alt = decode_raw_mutation(mut)

    items = []

    for isoform in iterate_affected_isoforms(gene_name, ref, pos, alt):

        mutation, created = get_or_create(
            Mutation,
            protein=isoform,
            position=pos,
            alt=alt
        )

        items.append(
            SearchResult(
                protein=isoform,
                mutation=mutation,
                is_mutation_novel=created,
                type='proteomic',
                ref=ref,
                alt=alt,
                pos=pos,
            )
        )
    return items


def represent_mutation(mutation, data_filter, representation_type=dict):

    affected_sites = mutation.get_affected_ptm_sites(data_filter)

    return representation_type(
        (
            ('pos', mutation.position),
            ('alt', mutation.alt),
            ('ref', mutation.ref),
            ('cnt_ptm', len(affected_sites)),
            ('sites', [
                site.to_json(with_kinases=True)
                for site in affected_sites
            ])
        )
    )


def drugs_interacting_with_kinases(filter_manager, kinases):
    from sqlalchemy import and_

    kinase_gene_ids = [kinase.protein.gene_id for kinase in kinases if kinase.protein]
    drugs = filter_manager.query_all(
        Drug,
        lambda q: and_(
            q,
            Gene.id.in_(kinase_gene_ids)
        ),
        lambda query: query.join(Drug.target_genes)
    )
    drugs_by_kinase = defaultdict(set)
    for drug in drugs:
        for target_gene in drug.target_genes:
            drugs_by_kinase[target_gene].add(drug)
    return drugs_by_kinase