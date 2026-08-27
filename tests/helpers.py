import builtins


ORIGINAL_IMPORT = builtins.__import__


def import_without_boto3(name, *args, **kwargs):
    if name == "boto3":
        raise ImportError("forced offline Bedrock fallback")
    return ORIGINAL_IMPORT(name, *args, **kwargs)


def import_without_yfinance(name, *args, **kwargs):
    if name == "yfinance":
        raise ImportError("forced offline yfinance fallback")
    return ORIGINAL_IMPORT(name, *args, **kwargs)
