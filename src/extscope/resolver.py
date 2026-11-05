"""Explicit scope files/index; no global search paths, authority or environment."""
import errno
import os
from types import MappingProxyType
from .io import read_text_file
from .declaration import MAX_DOCUMENT_BYTES, ScopeDocument, ScopeError, parse_document


def load_document(path: str | os.PathLike) -> ScopeDocument:
    if os.path.islink(path):        # named diagnostic for the O_NOFOLLOW refusal below (otherwise a bare ELOOP)
        raise OSError(errno.ELOOP, 'SYMLINK_REJECTED: a scope file must be a regular file, not a symlink '
                      '(its bytes are the authority; a link lets them change or live outside the granted root)')
    try:
        source = read_text_file(path, max_bytes=MAX_DOCUMENT_BYTES)
    except ValueError as error:
        raise ScopeError(str(error)) from None
    return parse_document(source, origin=os.fspath(path))


class ScopeIndex:
    """An explicit immutable catalog, rejecting UUID/name ambiguity globally."""
    def __init__(self, documents):
        self.documents = tuple(documents)
        aliases = {}
        uuids = set()
        for document in self.documents:
            if not isinstance(document, ScopeDocument) or document.uuid in uuids:
                raise ScopeError('duplicate or invalid scope identity')
            uuids.add(document.uuid)
            for alias in (document.uuid, document.name):
                if alias in aliases and aliases[alias] is not document:
                    raise ScopeError('ambiguous scope identity')
                aliases[alias] = document
        self._aliases = MappingProxyType(aliases)

    @classmethod
    def from_files(cls, paths):
        if isinstance(paths, (str, bytes, os.PathLike)):
            raise ScopeError('scope files must be an explicit iterable of paths')
        return cls(load_document(path) for path in paths)

    def resolve(self, reference: str) -> ScopeDocument:
        if not isinstance(reference, str) or reference not in self._aliases:
            raise ScopeError('scope reference is not in explicit catalog')
        return self._aliases[reference]
