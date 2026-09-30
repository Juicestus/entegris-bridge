#include "Util.h"

void PrintBytes(const char* label, const byte* buf, size_t len)
{
	printf("%s %zu bytes\n", label, len);
	if (!buf || !len) return;

	char ascii[17];
	size_t i;
	for (i = 0; i < len; i++)
	{
		if (i % 16 == 0)	printf("    %04zx ", i);

		printf(" %02x", buf[i]);
		ascii[i % 16] = (buf[i] >= 32 && buf[i] < 127) ? (char)buf[i] : '.';

		if (i % 16 == 15)
		{
			ascii[16] = 0;
			printf("  |%s|\n", ascii);
		}
	}

	// pad the last short line so the ascii column stays put
	if (i % 16)
	{
		ascii[i % 16] = 0;
		for (size_t j = i % 16; j < 16; j++)	printf("   ");
		printf("  |%s|\n", ascii);
	}
}