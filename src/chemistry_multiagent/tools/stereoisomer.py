from typing import List, Optional

from rdkit import Chem
from rdkit.Chem.EnumerateStereoisomers import EnumerateStereoisomers, StereoEnumerationOptions


def enumerate_stereoisomers(
    smiles: str,
    try_embedding: bool = True,
    unique: bool = True,
    max_isomers: Optional[int] = None,
) -> List[str]:
    """
    Name: enumerate_stereoisomers
    Description: Enumerate stereoisomeric SMILES (chiral centers and E/Z double bonds) from an input SMILES.
    Parameters:
    smiles: str Input molecule in SMILES format.
    try_embedding: bool Whether to try 3D embedding during enumeration.
    unique: bool Whether to return unique stereoisomers only.
    max_isomers: Optional[int] Maximum number of isomers to return. None keeps RDKit default cap.
    Returns:
    List[str] A list of stereoisomeric SMILES.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Failed to parse SMILES: {smiles}")

    # Keep legacy behavior: RDKit internal cap defaults to 100 when user does not specify.
    options_max_isomers = 100 if max_isomers is None else max_isomers
    opts = StereoEnumerationOptions(
        maxIsomers=options_max_isomers,
        tryEmbedding=try_embedding,
        unique=unique,
    )

    isomers = list(EnumerateStereoisomers(mol, options=opts))
    smiles_list = [Chem.MolToSmiles(iso, isomericSmiles=True) for iso in isomers]
    return smiles_list
