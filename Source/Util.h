#pragma once

#ifdef _WIN32
#define _CRT_SECURE_NO_WARNINGS	// TODO: compat layer
#endif

#include <iostream>
#include <fstream>
#include <string>
#include <algorithm>

#include <cstdio>
#include <cstdint>
#include <cassert>
#include <cstring>

#ifdef _WIN32

#define _WINSOCK_DEPRECATED_NO_WARNINGS
#define NOMINMAX

#include <winsock2.h>
#include <ws2tcpip.h>
#include <Windows.h>
#pragma comment(lib, "ws2_32.lib")

using ssize_t = SSIZE_T;

#else
/* unix specific */
#endif

using uint = unsigned int;
using ushort = unsigned short;

#define FATAL(FMT, ...)	fprintf(stderr, FMT "\n", __VA_ARGS__)
#define ARR_LEN(A)		(sizeof(A) / sizeof(*A))

#define BOOLTF(BOOL)	( (BOOL) ? "TRUE" : "FALSE" )		// bool -> true/false string
#define BOOLYN(BOOL)	( (BOOL) ? "YES" : "NO" )			// bool -> yes/no string 


#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__)
// should never err 
#error "Big-endian hosts are not supported"		
#endif


#ifndef FORCEINLINE
#if defined(_MSC_VER)
#define FORCEINLINE		__forceinline
#elif defined(__GNUC__) || defined(__clang__)
#define FORCEINLINE		inline __attribute__((always_inline))
#else
#define FORCEINLINE		inline
#endif
#endif

#if defined(_MSC_VER)
#define BSWAP16(x)		_byteswap_ushort(x)
#define BSWAP32(x)		((uint32_t)_byteswap_ulong(x))
#elif defined(__GNUC__) || defined(__clang__)
#define BSWAP16(x)		__builtin_bswap16(x)
#define BSWAP32(x)		__builtin_bswap32(x)
#else
#define BSWAP16(x)		((uint16_t)(((x) >> 8) | ((x) << 8)))
#define BSWAP32(x)		(((x) >> 24) | (((x) >> 8) & 0x0000FF00u) | \
							 (((x) << 8) & 0x00FF0000u) | ((x) << 24))
#endif



