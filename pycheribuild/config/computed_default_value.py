#
# Copyright (c) 2017 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
import typing
from typing import Any, Callable, Union

T = typing.TypeVar("T")
if typing.TYPE_CHECKING:
    from ..utils import ConfigBase
    ConfigTy = typing.TypeVar("ConfigTy", bound=ConfigBase)


class ComputedDefaultValue(typing.Generic[T]):
    def __init__(self, function: "Callable[[ConfigTy, Any], T]",
                 as_string: "Union[str, Callable[[Any], str]]",
                 as_readme_string: "Union[str, Callable[[Any], str], None]" = None,
                 inherit: "typing.Optional[ComputedDefaultValue[T]]" = None):
        if inherit is not None:
            def inheriting_function(config, project):
                val = function(config, project)
                if val is None:
                    val = inherit.function(config, project)
                return val
            self.function = inheriting_function
        else:
            assert function is not None, "Must provide function or inherit"
            self.function = function

        if inherit is not None:
            assert callable(as_string), "Inheriting only makes sense with callable as_string"

            if not callable(inherit.as_string):
                def inherit_as_string_wrapper(cls):
                    return inherit.as_string
                inherited_as_string = inherit_as_string_wrapper
            else:
                inherited_as_string = inherit.as_string

            def inheriting_as_string(cls):
                val = as_string(cls)
                if val is not None:
                    return val
                return inherited_as_string(cls)

            self.as_string = inheriting_as_string
        else:
            assert function is not None, "Must provide as_string or inherit"
            self.as_string = as_string

        if inherit is not None:
            if not callable(as_readme_string):
                assert as_readme_string is None, "Inheriting only makes sense with callable or None as_readme_string"

                def as_readme_string_none_wrapper(cls):
                    return None
                as_readme_string = as_readme_string_none_wrapper

            if not callable(inherit.as_readme_string):
                def inherit_as_readme_string_wrapper(cls):
                    return inherit.as_readme_string
                inherited_as_readme_string = inherit_as_readme_string_wrapper
            else:
                inherited_as_readme_string = inherit.as_readme_string

            # Prefer using the overridden as_string rather than the inherited
            # as_readme_string so you only need to override as_readme_string if
            # you need to use it yourself, rather than to avoid using an
            # inherited one not consistent with your as_string
            def inheriting_as_readme_string(cls):
                assert callable(as_readme_string)
                val = as_readme_string(cls)
                if val is not None:
                    return val
                val = as_string(cls)
                if val is not None:
                    return val
                val = inherited_as_readme_string(cls)
                if val is not None:
                    return val
                return inherited_as_string(cls)

            self.as_readme_string = inheriting_as_readme_string
        else:
            self.as_readme_string = as_readme_string

    def __call__(self, config, obj) -> T:
        return self.function(config, obj)

    def __repr__(self) -> str:
        return "{ComputedDefault:" + str(self.as_string) + "}"
