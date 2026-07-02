import paperscraper

from path_utils import TEST_INPUTS_DIR

papers = paperscraper.search_papers('bayesian model selection',
                                    limit=10,
                                    pdir=str(TEST_INPUTS_DIR / "papers"))
